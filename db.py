"""
Database access for solver.py (SQL Server via pyodbc).
Fill in .env and edit the queries marked EDIT, then set USE_DB = True in solver.py.
"""
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Connection settings live in the .env file next to this script (DB_SERVER, DB_NAME,
# DB_USER, DB_PASSWORD, DB_DRIVER, INST_ID). Never hard-code the password here.
INST_ID = int(os.getenv("INST_ID") or 1)   # Inst_Id from GenExmTxGalley

# COURSE SUPPORT (optional). The course id typed by the user is kept and passed in as the "?" below.
# Leave these empty until you know which table holds courses; the run then skips the course
# check and does NOT filter students by course.
COURSE_EXISTS_SQL = ""   # e.g. "SELECT COUNT(*) FROM YourCourseTable WHERE Course_Id = ?"
COURSE_JOIN_SQL = ""     # e.g. "JOIN YourStudentTable s ON s.roll_no = g.roll_no"
COURSE_WHERE_SQL = ""    # e.g. "AND s.Course_Id = ?"

# Parameters, in order: Inst_Id, proc_year, proc_sem, (course_id only if COURSE_WHERE_SQL is set)
ENROLLMENT_SQL = """
SELECT g.roll_no, g.subj_code, g.attempt_no, g.ilrs_tag
FROM GenExmTxGalley g
{join}
WHERE g.Inst_Id = ? AND g.proc_year = ? AND g.proc_sem = ?
  AND g.confirm_tag = 'Y'
  AND g.debar_type IS NULL
  {where}
"""

# Mapping table holds (Group_ID, subj_code, Active_Tag); master holds (Group_ID, regular_code, regular_name)
GROUPS_SQL = """
SELECT m.Group_ID, m.subj_code
FROM GenExmTxExamSubjGrp_Mapping m
JOIN GenExmTxExamSubj_Group g ON g.Group_ID = m.Group_ID
WHERE m.Active_Tag = 'Y'
"""
# ===========================================================================


def _env(name, default=None):
    v = os.getenv(name) or default
    if not v:
        raise ValueError(f"Missing {name} in the .env file.")
    return v


def connect():
    pw = _env("DB_PASSWORD").replace("}", "}}")   # braces protect ; and other special characters
    cs = (f"DRIVER={{{_env('DB_DRIVER', 'ODBC Driver 17 for SQL Server')}}};"
          f"SERVER={_env('DB_SERVER')};DATABASE={_env('DB_NAME')};"
          f"UID={_env('DB_USER')};PWD={{{pw}}};"
          "Encrypt=yes;TrustServerCertificate=yes;")
    import pyodbc
    return pyodbc.connect(cs)


_course_note_shown = False


def course_exists(course_id):
    global _course_note_shown
    if not COURSE_EXISTS_SQL:
        if not _course_note_shown:
            print("NOTE: course check not configured; course ids were not verified.")
            _course_note_shown = True
        return True
    with connect() as conn:
        return conn.cursor().execute(COURSE_EXISTS_SQL, course_id).fetchone()[0] > 0


def missing_subjects(codes):
    """Subject codes that do not exist in GenTMSubject (master data check)."""
    codes, found = sorted(set(codes)), set()
    with connect() as conn:
        cur = conn.cursor()
        for i in range(0, len(codes), 1000):
            chunk = codes[i:i + 1000]
            q = "SELECT SUBJ_CODE FROM GenTMSubject WHERE SUBJ_CODE IN (%s)" % ",".join("?" * len(chunk))
            found |= {str(r[0]).strip() for r in cur.execute(q, *chunk).fetchall()}
    return [c for c in codes if c not in found]


def fetch_groups():
    """Returns {subject_code: group_id}, same shape the solver expects."""
    mapping = {}
    with connect() as conn:
        for group_id, code in conn.cursor().execute(GROUPS_SQL).fetchall():
            code = str(code).strip()
            if code in mapping and mapping[code] != group_id:
                raise ValueError(f"Subject {code} appears in two groups")
            mapping[code] = group_id
    return mapping


def fetch_enrollments(proc_yy, proc_sem, course_ids):
    """Enrollments for one or more courses, combined (duplicates removed)."""
    sql = ENROLLMENT_SQL.format(join=COURSE_JOIN_SQL, where=COURSE_WHERE_SQL)
    if COURSE_WHERE_SQL:
        runs = list(course_ids)
    else:
        print("NOTE: course filter not configured; using ALL confirmed students for this year/semester.")
        runs = [None]
    seen, out = set(), []
    with connect() as conn:
        cur = conn.cursor()
        for cid in runs:
            params = [INST_ID, proc_yy, proc_sem] + ([cid] if cid is not None else [])
            for r in cur.execute(sql, *params).fetchall():
                row = {"roll_no": str(r[0]).strip(), "subj_code": str(r[1]).strip(),
                       "attempt_no": r[2], "ilrs_tag": r[3], "course_id": cid or "*"}
                key = (row["roll_no"], row["subj_code"], row["attempt_no"])
                if key not in seen:
                    seen.add(key)
                    out.append(row)
    if not out:
        raise ValueError(f"No enrollments found in GenExmTxGalley for proc_year={proc_yy}, "
                         f"proc_sem={proc_sem}, course(s) {', '.join(course_ids)}.")
    missing = missing_subjects(r["subj_code"] for r in out)
    if missing:
        print(f"WARNING: {len(missing)} subject code(s) in Galley are not in GenTMSubject: {missing[:10]}")
    return out


def write_timetable(rows, proc_yy, proc_sem):
    """All-or-nothing. Replaces only the subjects being written, so other courses' dates are untouched."""
    conn = connect()
    try:
        cur = conn.cursor()
        for r in rows:
            exam_date = datetime.strptime(r["exam_date"], "%Y-%m-%d")   # real datetime, not text
            cur.execute("DELETE FROM GenExmTxExamDate WHERE proc_yy = ? AND proc_sem = ? AND subj_code = ?",
                        proc_yy, proc_sem, r["subj_code"])
            cur.execute("INSERT INTO GenExmTxExamDate (proc_yy, proc_sem, subj_code, exam_date, exam_session, Session_Order) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        proc_yy, proc_sem, r["subj_code"], exam_date, r["exam_session"], r["session_order"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def self_test():
    """Run with:  python db.py   (checks the connection and reads GenTMSubject)."""
    try:
        with connect() as conn:
            cur = conn.cursor()
            print("Connected to database:", cur.execute("SELECT DB_NAME()").fetchone()[0])
            print("GenTMSubject rows:", cur.execute("SELECT COUNT(*) FROM GenTMSubject").fetchone()[0])
            cur.execute("SELECT TOP 3 * FROM GenTMSubject")
            print("GenTMSubject columns:", ", ".join(c[0] for c in cur.description))
            for r in cur.fetchall():
                print("  sample:", r[0], "|", r[1])
    except Exception as e:
        print("FAILED:", e)


if __name__ == "__main__":
    self_test()