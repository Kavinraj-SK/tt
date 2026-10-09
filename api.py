"""
Fill the local group tables from the exam API.

    GenExmTxExamSubj_Group          (Group_ID, regular_code, regular_name)
    GenExmTxExamSubjGrp_Mapping     (Group_ID, subj_code, subj_name, Active_Tag, CreatedBy, Created_Date, ...)

Run:  python api_sync.py
Reads only API_TOKEN (and the DB_* values db.py uses) from the .env file.
Everything else (server URL, endpoint, filters) is set in the SETTINGS block below.

It shows a preview and asks y/n before writing. The write is all-or-nothing.
"""
import json
import os
import sys
from datetime import datetime

import requests
from dotenv import load_dotenv

import db

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))
RAW_FILE = os.path.join(HERE, "api_response.json")   # last raw response, for debugging


# ------------------------------- SETTINGS (edit) ---------------------------
API_BASE_URL = "https://your-server"                         # EDIT: your API server, no trailing slash
API_ENDPOINT = "/api/ExamTimeTable/GetDeptWiseTimeTable"     # EDIT if groups come from another endpoint
API_EXAM_TYPE = "R"
API_STR_MONTH = ""
API_PRAC_TAG = "N"
# The token is the only thing read from .env:  API_TOKEN=...


def get_token():
    token = os.getenv("API_TOKEN")
    if not token:
        raise ValueError("Missing API_TOKEN in the .env file.")
    return token


def fetch(proc_year, proc_sem):
    if "your-server" in API_BASE_URL:
        raise ValueError("Set API_BASE_URL at the top of api_sync.py first.")
    token = get_token()
    url = API_BASE_URL.rstrip("/") + "/" + API_ENDPOINT.lstrip("/")
    params = {"procYear": proc_year, "procSem": proc_sem, "examType": API_EXAM_TYPE,
              "strMonth": API_STR_MONTH, "pracTag": API_PRAC_TAG}
    try:
        r = requests.get(url, params=params, timeout=60,
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    except requests.RequestException as e:
        raise RuntimeError(f"Could not reach the API at {url}: {e}")
    if r.status_code in (401, 403):
        raise RuntimeError(f"API refused the token (HTTP {r.status_code}). Check API_TOKEN in .env; it may have expired.")
    if r.status_code != 200:
        raise RuntimeError(f"API returned HTTP {r.status_code}: {r.text[:300]}")
    try:
        payload = r.json()
    except ValueError:
        raise RuntimeError(f"API did not return JSON: {r.text[:300]}")
    json.dump(payload, open(RAW_FILE, "w"), indent=2)
    return payload


# ------------------------------- parsing ----------------------------------
# EDIT HERE if the API uses different field names. Keys are compared ignoring case, _ and spaces.
FIELD_ALIASES = {
    "group_id":     ["groupid", "group_id", "grpid"],
    "regular_code": ["regularcode", "regular_code", "regularsubjcode", "regsubjcode"],
    "regular_name": ["regularname", "regular_name", "regularsubjname", "regsubjname"],
    "subj_code":    ["subjcode", "subj_code", "subjectcode"],
    "subj_name":    ["subjname", "subj_name", "subjectname"],
}


def _norm(k):
    return str(k).replace("_", "").replace(" ", "").lower()


def _records(payload):
    """Finds the list of records whatever the wrapper is (list, {data: [...]}, {result: {items: [...]}})."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        for v in payload.values():
            if isinstance(v, dict):
                found = _records(v)
                if found:
                    return found
    return []


def extract_groups(payload):
    """Returns (groups, mappings): groups = {gid: (regular_code, regular_name)}, mappings = {(gid, subj_code): subj_name}."""
    recs = _records(payload)
    if not recs:
        raise ValueError("The API returned no records, so no groups can be built.")
    lookup = {_norm(k): k for k in recs[0]}
    col = {}
    for field, aliases in FIELD_ALIASES.items():
        for a in aliases:
            if a in lookup:
                col[field] = lookup[a]
                break
    if "group_id" not in col or "subj_code" not in col:
        raise ValueError("This response has no group fields. Fields found in the first record:\n  "
                         + ", ".join(recs[0].keys())
                         + "\nThis endpoint may not return groups. Paste these field names to Claude, or edit FIELD_ALIASES.")
    groups, mappings = {}, {}
    for r in recs:
        gid, code = r.get(col["group_id"]), r.get(col["subj_code"])
        if gid in (None, "") or code in (None, ""):
            continue
        code = str(code).strip()
        reg_code = str(r.get(col.get("regular_code", ""), "") or "").strip() or code
        reg_name = str(r.get(col.get("regular_name", ""), "") or "").strip()
        groups.setdefault(gid, (reg_code, reg_name))
        mappings[(gid, code)] = str(r.get(col.get("subj_name", ""), "") or "").strip()
    if not groups:
        raise ValueError("Records were returned but none had both a group id and a subject code.")
    return groups, mappings


# ------------------------------- writing ----------------------------------
def write(groups, mappings):
    """All-or-nothing: replaces the rows of exactly these Group_IDs."""
    now = datetime.now()
    conn = db.connect()
    try:
        cur = conn.cursor()
        for gid, (reg_code, reg_name) in groups.items():
            cur.execute("DELETE FROM GenExmTxExamSubjGrp_Mapping WHERE Group_ID = ?", gid)
            cur.execute("DELETE FROM GenExmTxExamSubj_Group WHERE Group_ID = ?", gid)
            cur.execute("INSERT INTO GenExmTxExamSubj_Group (Group_ID, regular_code, regular_name) VALUES (?, ?, ?)",
                        gid, reg_code, reg_name)
        for (gid, code), name in mappings.items():
            cur.execute("INSERT INTO GenExmTxExamSubjGrp_Mapping "
                        "(Group_ID, subj_code, subj_name, Active_Tag, CreatedBy, Created_Date) "
                        "VALUES (?, ?, ?, 'Y', 'api_sync', ?)", gid, code, name, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    proc_year = int(input("procYear (e.g. 2025): ").strip())
    proc_sem = int(input("procSem  (e.g. 1): ").strip())
    payload = fetch(proc_year, proc_sem)
    print(f"Fetched OK (raw response saved to {os.path.basename(RAW_FILE)}).")
    groups, mappings = extract_groups(payload)
    print(f"\nFound {len(groups)} group(s) and {len(mappings)} subject mapping(s). Preview:")
    for gid in list(groups)[:5]:
        subs = [c for (g, c) in mappings if g == gid]
        print(f"  Group {gid}: {groups[gid][0]} -> {', '.join(subs)}")
    if input("\nWrite these into the local group tables? (y/n): ").strip().lower() in ("y", "yes"):
        write(groups, mappings)
        print("Saved to GenExmTxExamSubj_Group and GenExmTxExamSubjGrp_Mapping.")
    else:
        print("Nothing written.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError) as e:
        sys.exit(f"ERROR: {e}")
    except Exception as e:
        sys.exit(f"ERROR: {type(e).__name__}: {e}")