"""Asks for the inputs, then runs Test.GenPrIUExamTimetable_final and shows its result.
Uses db.py (same folder) for the connection, so .env is the only configuration.
The procedure writes into GenExmTxExamDate inside a transaction; nothing is kept until you answer y."""
import sys
from datetime import datetime
from collections import OrderedDict

PROC = "Test.GenPrIUExamTimetable_final"


def ask_date(prompt):
    while True:
        try:
            return datetime.strptime(input(prompt).strip(), "%Y-%m-%d").date()
        except ValueError:
            print("  Please enter the date as YYYY-MM-DD.")


def ask_int(prompt, minimum=0, default=None):
    while True:
        raw = input(prompt).strip()
        if raw == "" and default is not None:
            return default
        if raw.isdigit() and int(raw) >= minimum:
            return int(raw)
        print(f"  Please enter a whole number >= {minimum}.")


def ask_choice(prompt, low, high):
    while True:
        raw = input(prompt).strip()
        if raw.isdigit() and low <= int(raw) <= high:
            return int(raw)
        print(f"  Please enter a number from {low} to {high}.")


def ask_yes_no(prompt):
    while True:
        a = input(prompt).strip().lower()
        if a in ("y", "yes"):
            return True
        if a in ("n", "no"):
            return False
        print("  Please type y or n.")


def cycles(cur, db):
    """Cycles that have registered, confirmed enrollments (the procedure does not look at the institute)."""
    cur.execute(
        "SELECT g.proc_year, g.proc_sem, COUNT(DISTINCT g.roll_no), COUNT(DISTINCT g.subj_code), COUNT(DISTINCT g.Inst_Id) "
        f"FROM {db.GALLEY} g WHERE g.register_tag = 'R' AND g.confirm_tag = 'Y' "
        "GROUP BY g.proc_year, g.proc_sem ORDER BY g.proc_year DESC, g.proc_sem DESC")
    return cur.fetchall()


def get_inputs(cur, db):
    cyc = cycles(cur, db)
    if not cyc:
        raise ValueError("No registered and confirmed enrollments found in " + db.GALLEY)
    print("Cycles with enrollments, latest first:")
    for i, (y, s, studs, subs, insts) in enumerate(cyc, 1):
        print(f"  {i}) {y} sem {s}: {studs} students, {subs} subjects, {insts} institute(s)")
    k = ask_choice("Cycle number (0 = latest): ", 0, len(cyc))
    year, sem = (cyc[0] if k == 0 else cyc[k - 1])[:2]
    print(f"Using {year} sem {sem}")

    cur.execute(
        "SELECT s.subj_type, COUNT(DISTINCT g.subj_code) "
        f"FROM {db.GALLEY} g JOIN {db.T('GenTMSubject')} s ON s.subj_code = g.subj_code "
        "WHERE g.proc_year = ? AND g.proc_sem = ? AND g.register_tag = 'R' AND g.confirm_tag = 'Y' "
        "GROUP BY s.subj_type ORDER BY s.subj_type", year, sem)
    types = [(str(t).strip(), n) for t, n in cur.fetchall()]
    if not types:
        raise ValueError("No subjects of any type found for this cycle in GenTMSubject.")
    print("\nSubject types in this cycle:")
    for i, (t, n) in enumerate(types, 1):
        print(f"  {i}) {t}  ({n} subjects)")
    subj_type = types[ask_choice("Subject type number: ", 1, len(types)) - 1][0]

    cur.execute(
        f"SELECT DISTINCT g.ilrs_tag FROM {db.GALLEY} g WHERE g.proc_year = ? AND g.proc_sem = ? "
        "AND g.register_tag = 'R' AND g.confirm_tag = 'Y' ORDER BY g.ilrs_tag", year, sem)
    tags = [str(r[0]).strip() for r in cur.fetchall() if r[0] is not None and str(r[0]).strip()]
    ilrs = None
    if tags:
        raw = input(f"ILRS tag ({', '.join(tags)}) - press Enter for all: ").strip().upper()
        if raw:
            if raw not in tags:
                raise ValueError(f"ILRS tag must be one of: {', '.join(tags)}")
            ilrs = raw

    raw = input("Course nature (whole number) - press Enter for all: ").strip()
    nature = int(raw) if raw.isdigit() else None
    if raw and nature is None:
        raise ValueError("Course nature must be a whole number.")

    start = ask_date("Start date (YYYY-MM-DD): ")
    while True:
        end = ask_date("End date   (YYYY-MM-DD): ")
        if end >= start:
            break
        print("  End date must be on or after the start date.")
    interval = ask_int("Interval between exam dates (0 = every working day, 1 = every other day) [1]: ", 0, 1)
    return year, sem, ilrs, subj_type, start, end, interval, nature


def run(cur, p):
    year, sem, ilrs, subj_type, start, end, interval, nature = p
    cur.execute(f"SET NOCOUNT ON; EXEC {PROC} ?,?,?,?,?,?,?,?,?",
                year, sem, ilrs, subj_type, start, end, interval, nature, STUD_STATUS)
    sets = []
    while True:
        if cur.description:
            cols = [c[0] for c in cur.description]
            sets.append((cols, cur.fetchall()))
        if not cur.nextset():
            break
    return sets


def show(sets, year, sem):
    timetable = next(((c, r) for c, r in sets if "proc_yy" in c and "regular_code" in c), None)
    status = next(((c, r) for c, r in sets if "Status" in c), None)
    if timetable:
        c, rows = timetable
        i = {n: c.index(n) for n in ("subj_code", "subj_name", "exam_date", "exam_session")}
        print(f"\nTimetable: {len(rows)} subjects")
        for r in sorted(rows, key=lambda r: (str(r[i['exam_date']]), r[i['exam_session']])):
            print(f"  {str(r[i['exam_date']])[:10]}  {r[i['exam_session']]:<9} {r[i['subj_code']]}  {r[i['subj_name']]}")
    if status:
        c, rows = status
        d = dict(zip(c, rows[0]))
        print(f"\nStatus: {d['Status']}")
        if d["Status"] == "SUCCESS":
            print("  No clashes.")
        else:
            print(f"  Clash instances: {d['TotalClashInstances']}, students affected: {d['StudentsWithClash']}")
            print(f"  {d['Recommendation']}")
    return status and dict(zip(status[0], status[1][0]))["Status"]


def main():
    import db
    global STUD_STATUS
    STUD_STATUS = (db.STUD_STATUS or "A")[:1] if db.STUD_STATUS != "*" else "A"
    conn = db.connect()
    try:
        cur = conn.cursor()
        params = get_inputs(cur, db)
        print("\nRunning the procedure...")
        try:
            sets = run(cur, params)
        except Exception as e:
            conn.rollback()
            msg = str(e.args[1] if len(e.args) > 1 else e)
            raise ValueError(msg.split("]")[-1].strip() if "]" in msg else msg)
        outcome = show(sets, params[0], params[1])
        if ask_yes_no("\nKeep these dates in GenExmTxExamDate? (y/n): "):
            conn.commit()
            print("Saved.")
        else:
            conn.rollback()
            print("Not saved (rolled back).")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        sys.exit(f"ERROR: {e}")
    except Exception as e:
        sys.exit(f"ERROR: {type(e).__name__}: {e}")