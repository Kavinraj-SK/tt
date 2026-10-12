"""
Exam timetable solver (OR-Tools CP-SAT).  Run:  python solver.py

Data comes from sample files (USE_DB = False) or your database (USE_DB = True, see db.py).

It asks for:
  1. Start date
  2. End date
  3. Minimum gap (in days) between any two exams of the same student
  4. Course id(s), comma separated -> error if any does not exist in the DB

RA rule: handled INSIDE the model. A student's RA papers and current papers are
all treated as papers that student must write, so none of them can share a slot
(and with gap > 0, they must also be that many days apart).
"""
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import combinations

from dotenv import load_dotenv
from ortools.sat.python import cp_model

# ---------------------------- settings -----------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))
GROUPS_FILE = os.path.join(HERE, "sample_groups.json")
ENROLL_FILE = os.path.join(HERE, "sample_enrollments.csv")   # needs a course_id column
COURSES_FILE = os.path.join(HERE, "sample_courses.json")     # used only when DB_CONN_STR is None
OUT_FILE = os.path.join(HERE, "timetable.json")

PROC_YY, PROC_SEM = 2025, 1
SESSIONS = ["F", "A"]
SKIP_WEEKDAYS = {6}  

# Set USE_DB in the .env file: true = real database (default), false = local sample files.
USE_DB = os.getenv("USE_DB", "true").strip().lower() in ("1", "true", "yes", "y")


class CourseNotFoundError(Exception):
    pass


def is_ra(row):
    """ASSUMPTION: attempt_no > 1 means arrear. Change if your data uses ilrs_tag."""
    return int(row["attempt_no"]) > 1


# ---------------------------- user input ---------------------------------
def ask_date(prompt):
    while True:
        try:
            return datetime.strptime(input(prompt).strip(), "%Y-%m-%d").date()
        except ValueError:
            print("  Please enter the date as YYYY-MM-DD.")


def ask_int(prompt, minimum=0):
    while True:
        try:
            v = int(input(prompt).strip())
            if v >= minimum:
                return v
        except ValueError:
            pass
        print(f"  Please enter a whole number >= {minimum}.")


def ask_yes_no(prompt):
    while True:
        a = input(prompt).strip().lower()
        if a in ("y", "yes"):
            return True
        if a in ("n", "no"):
            return False
        print("  Please type y or n.")


def ask_subject_gaps(subjects, default_gap):
    """Gap (days) for each subject = minimum days between a student's previous exam and THIS subject.
    Enter keeps the default."""
    gaps = {c: default_gap for c in subjects}
    ans = input(f"\nSet a different gap for individual subjects? Default is {default_gap} day(s) for all. (y/n): ")
    if ans.strip().lower() not in ("y", "yes"):
        return gaps
    print("For each subject enter the minimum days between a student's previous exam and this subject.")
    print("Press Enter to keep the default.\n")
    for c in sorted(subjects):
        while True:
            raw = input(f"  Gap days before {c} [{default_gap}]: ").strip()
            if raw == "":
                break
            if raw.isdigit():
                gaps[c] = int(raw)
                break
            print("    Please enter a whole number, or press Enter.")
    return gaps


def course_exists(course_id):
    if USE_DB:
        import db
        return db.course_exists(course_id)
    return course_id in json.load(open(COURSES_FILE))


def get_inputs():
    start = ask_date("Start date (YYYY-MM-DD): ")
    while True:
        end = ask_date("End date   (YYYY-MM-DD): ")
        if end >= start:
            break
        print("  End date must be on or after the start date.")
    gap = ask_int("Default gap between a student's exams in days (you can change it per subject next)\n"
                  "  (0 = same day allowed in a different session, 1 = different days,\n"
                  "   2 = at least one free day between exams): ")
    raw = input("Course id(s), separated by commas: ")
    course_ids = list(dict.fromkeys(c.strip() for c in raw.split(",") if c.strip()))
    if not course_ids:
        raise CourseNotFoundError("No course id entered.")
    missing = [c for c in course_ids if not course_exists(c)]
    if missing:
        raise CourseNotFoundError(f"Course id(s) not found in the database: {', '.join(missing)}")
    return start, end, gap, course_ids


# ---------------------------- loaders ------------------------------------
def load_groups(path):
    data = json.load(open(path))
    subj_to_group = {}
    for g in data["groups"]:
        if not g.get("active", True):
            continue
        for s in g["subjects"]:
            code = s["code"]
            if code in subj_to_group and subj_to_group[code] != g["group_id"]:
                raise ValueError(f"Subject {code} appears in two groups")
            subj_to_group[code] = g["group_id"]
    return subj_to_group


def load_enrollments(path, course_ids):
    with open(path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("course_id") in course_ids]
    if not rows:
        raise ValueError(f"No enrollments found for course(s): {', '.join(course_ids)}.")
    return rows


def build_slots(start, end):
    slots, d = [], start
    while d <= end:
        if d.weekday() not in SKIP_WEEKDAYS:
            slots += [{"date": d.isoformat(), "session": s} for s in SESSIONS]
        d += timedelta(days=1)
    if not slots:
        raise ValueError("No exam slots in that date range.")
    return slots


# ---------------------------- model --------------------------------------
def solve(subj_to_group, enrollments, slots, gaps, time_limit=30):
    def unit_of(code):
        return f"G{subj_to_group[code]}" if code in subj_to_group else code

    cur, ra = defaultdict(set), defaultdict(set)
    unit_codes = defaultdict(set)
    for r in enrollments:
        u = unit_of(r["subj_code"])
        unit_codes[u].add(r["subj_code"])
        (ra if is_ra(r) else cur)[r["roll_no"]].add(u)

    # A grouped unit needs the largest gap of any subject inside it.
    ugap = {u: max(gaps.get(c, 0) for c in codes) for u, codes in unit_codes.items()}
    any_gap = any(g > 0 for g in ugap.values())

    students = set(cur) | set(ra)
    warnings = [f"{s}: same paper/group as current and RA: {sorted(cur[s] & ra[s])}"
                for s in students if cur[s] & ra[s]]

    n = len(slots)
    day_ords = [date.fromisoformat(s["date"]).toordinal() for s in slots]
    model = cp_model.CpModel()
    slot = {u: model.NewIntVar(0, n - 1, u) for u in unit_codes}

    day = {}
    if any_gap:
        for u in unit_codes:
            day[u] = model.NewIntVar(min(day_ords), max(day_ords), "d_" + u)
            model.AddElement(slot[u], day_ords, day[u])

    # THE RA RULE (+ per-subject gap): every paper a student writes, RA or current, is constrained here.
    # For two papers a, b of one student: whichever comes first (by slot), the later one must be at
    # least gap[later] days after it. A gap of 0 still forces different slots.
    seen_sets, seen_pairs = set(), set()
    for roll in students:
        units = sorted(cur[roll] | ra[roll])
        if len(units) < 2:
            continue
        if len(units) > n:
            raise ValueError(f"{roll} writes {len(units)} papers but only {n} slots exist")
        if not any_gap:
            key = frozenset(units)
            if key not in seen_sets:
                seen_sets.add(key)
                model.AddAllDifferent([slot[u] for u in units])
        else:
            for a, b in combinations(units, 2):
                if (a, b) in seen_pairs:
                    continue
                seen_pairs.add((a, b))
                a_first = model.NewBoolVar(f"{a}_{b}")
                model.Add(slot[a] < slot[b]).OnlyEnforceIf(a_first)
                model.Add(day[b] - day[a] >= ugap[b]).OnlyEnforceIf(a_first)
                model.Add(slot[b] < slot[a]).OnlyEnforceIf(a_first.Not())
                model.Add(day[a] - day[b] >= ugap[a]).OnlyEnforceIf(a_first.Not())

    last = model.NewIntVar(0, n - 1, "last")
    model.AddMaxEquality(last, list(slot.values()))
    model.Minimize(last)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = 8
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, warnings, ("INFEASIBLE: not enough slots for these rules. "
                                "Extend the end date or reduce the gap days.")

    rows = []
    for u, v in slot.items():
        s = solver.Value(v)
        order = sum(1 for x in slots[:s] if x["date"] == slots[s]["date"]) + 1
        for code in sorted(unit_codes[u]):
            rows.append({"subj_code": code, "exam_date": slots[s]["date"],
                         "exam_session": slots[s]["session"], "session_order": order})
    return rows, warnings, solver.StatusName(status)


def verify(rows, enrollments, gaps):
    where = {r["subj_code"]: (r["exam_date"], r["exam_session"], r["session_order"]) for r in rows}
    papers = defaultdict(set)
    for e in enrollments:
        papers[e["roll_no"]].add(e["subj_code"])
    problems = []
    for roll, codes in papers.items():
        for a, b in combinations(sorted(codes), 2):
            if where[a][:2] == where[b][:2]:
                problems.append((roll, a, b, "same slot"))
                continue
            first, later = sorted((a, b), key=lambda c: (where[c][0], where[c][2]))
            diff = (date.fromisoformat(where[later][0]).toordinal()
                    - date.fromisoformat(where[first][0]).toordinal())
            if diff < gaps.get(later, 0):
                problems.append((roll, first, later, f"{later} needs {gaps.get(later, 0)} day(s) gap, got {diff}"))
    return problems


def main():
    start, end, gap, course_ids = get_inputs()
    if USE_DB:
        import db
        groups = db.fetch_groups()
        enrollments = db.fetch_enrollments(PROC_YY, PROC_SEM, course_ids)
    else:
        groups = load_groups(GROUPS_FILE)
        enrollments = load_enrollments(ENROLL_FILE, course_ids)
    slots = build_slots(start, end)

    if groups:
        print(f"Groups: {len(set(groups.values()))} active group(s) covering {len(groups)} subject(s).")
    else:
        print("NOTE: no active subject groups found (GenExmTxExamSubjGrp_Mapping / "
              "GenExmTxExamSubj_Group have no usable rows). Every subject will be scheduled independently.")
    students = {e["roll_no"] for e in enrollments}
    subjects = {e["subj_code"] for e in enrollments}
    filtered = (not USE_DB) or bool(__import__("db").COURSE_WHERE_SQL)
    scope = f"for course(s) {', '.join(course_ids)}" if filtered else "(ALL students; course id NOT applied)"
    print(f"Enrollments: {len(enrollments)} rows, {len(students)} students, {len(subjects)} subjects {scope}.")

    gaps = ask_subject_gaps(subjects, gap)
    rows, warnings, status = solve(groups, enrollments, slots, gaps)
    for w in warnings:
        print("WARNING:", w)
    if rows is None:
        sys.exit(status)

    problems = verify(rows, enrollments, gaps)
    if problems:
        sys.exit(f"Verification failed: {problems}")

    json.dump({"proc_yy": PROC_YY, "proc_sem": PROC_SEM, "course_ids": course_ids,
               "start_date": start.isoformat(), "end_date": end.isoformat(),
               "default_gap_days": gap, "subject_gap_days": gaps, "rows": rows}, open(OUT_FILE, "w"), indent=2)
    print(f"\n{status}: {len(rows)} subjects scheduled, 0 clashes -> {OUT_FILE}")
    for r in sorted(rows, key=lambda r: (r["exam_date"], r["session_order"])):
        print(r["exam_date"], r["exam_session"], r["subj_code"])

    if USE_DB:
        if ask_yes_no("\nWrite this timetable into GenExmTxExamDate? (y/n): "):
            import db
            db.write_timetable(rows, PROC_YY, PROC_SEM)
            print("Saved to GenExmTxExamDate.")
        else:
            print("Not written to the database.")


if __name__ == "__main__":
    try:
        main()
    except (CourseNotFoundError, ValueError) as e:
        sys.exit(f"ERROR: {e}")
    except FileNotFoundError as e:
        sys.exit(f"ERROR: file not found: {e.filename}\n(Sample files are only needed when USE_DB=false in .env. Set USE_DB=true to use the database.)")
    except Exception as e:
        sys.exit(f"ERROR: {type(e).__name__}: {e}")