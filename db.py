import os
from contextlib import closing
from datetime import datetime
import pyodbc
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)),'.env'))
_inst=os.getenv('INST_ID')
# Optional fallback only. The institute is normally read from the data (see course_cycles); nothing is assumed.
INST_ID=int(_inst) if _inst and _inst.strip().isdigit() else None
SCHEMA='Test'  # fixed: all tables are read from / written to the Test schema
def T(name):return f"[{SCHEMA}].[{name}]"
REGISTER_TAG=(os.getenv('REGISTER_TAG','R')or'').strip()
def _reg_sql():
	if not REGISTER_TAG:return''
	if not REGISTER_TAG.isalnum()or len(REGISTER_TAG)>3:raise ValueError('REGISTER_TAG in .env must be 1-3 letters or digits.')
	return f"AND g.register_tag = '{REGISTER_TAG}'"
GALLEY=T('GenExmTxGalleyTT')
EXAM_DATE=T('GenExmTxExamDate')
# From GenPrIUExamTimetable_final: GenTMCourse.cour_id <- SISTMSTUD.cour_id <- SISTMSTUD.roll_no = Galley.roll_no
COURSE_EXISTS_SQL=os.getenv('COURSE_EXISTS_SQL')or f"SELECT COUNT(*) FROM {T('GenTMCourse')} WHERE cour_id = ?"
COURSE_JOIN_SQL=os.getenv('COURSE_JOIN_SQL')or f"JOIN {T('SISTMSTUD')} st ON st.roll_no = g.roll_no"
# The procedure filters stud_status = ' ', but this data uses 'A' for active students. Set STUD_STATUS in .env to change it; set it to * to skip the check.
STUD_STATUS=os.getenv('STUD_STATUS','A')
def _status_sql():
	if STUD_STATUS=='*':return''
	if len(STUD_STATUS)!=1 or not (STUD_STATUS.isalnum() or STUD_STATUS==' '):raise ValueError('STUD_STATUS in .env must be a single letter/digit, or * to skip.')
	return f" AND st.stud_status = '{STUD_STATUS}'"
COURSE_WHERE_SQL=os.getenv('COURSE_WHERE_SQL')or"AND st.cour_id = ?"+_status_sql()
ENROLLMENT_SQL="\nSELECT g.roll_no, g.subj_code, g.attempt_no, g.ilrs_tag\nFROM "+GALLEY+" g\n{join}\nWHERE g.Inst_Id = ? AND g.proc_year = ? AND g.proc_sem = ?\n  {reg}\n  AND g.confirm_tag = 'Y'\n  AND g.debar_type IS NULL\n  {where}\n"
GROUPS_SQL="\nSELECT m.Group_ID, m.subj_code\nFROM "+T('GenExmTxExamSubjGrp_Mapping')+" m\nWHERE m.Active_Tag = 'A'\n"
def _env(name,default=None):
	v=os.getenv(name)or default
	if not v:raise ValueError(f"Missing {name} in the .env file.")
	return v
def connect():password=os.getenv('DB_PASSWORD','').replace('}','}}');driver=_env('DB_DRIVER','ODBC Driver 17 for SQL Server');cs=f"DRIVER={{{driver}}};SERVER={_env("DB_SERVER")};DATABASE={_env("DB_NAME")};UID={_env("DB_USER")};PWD={{{password}}};Encrypt=yes;TrustServerCertificate=yes;";return pyodbc.connect(cs,timeout=15)
_course_configured=False
_course_note_shown=False
def _configure_course_filter():
	global COURSE_EXISTS_SQL,COURSE_WHERE_SQL,_course_configured
	if _course_configured:return
	_course_configured=True
	if COURSE_WHERE_SQL:return
	with closing(connect())as conn:rows=conn.cursor().execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ? AND COLUMN_NAME LIKE '%course%'",GALLEY).fetchall()
	cols=[r[0]for r in rows];pick=None
	for preferred in('course_id','course_code','course'):
		for c in cols:
			if c.lower()==preferred:pick=c;break
		if pick:break
	if not pick and len(cols)==1:pick=cols[0]
	if pick:COURSE_WHERE_SQL=f"AND g.[{pick}] = ?";COURSE_EXISTS_SQL=f"SELECT COUNT(*) FROM {GALLEY} WHERE [{pick}] = ?";print(f"Course filter: using {GALLEY}.{pick}")
	elif cols:print(f"NOTE: several course-like columns in {GALLEY}: {cols}. Set COURSE_WHERE_SQL / COURSE_EXISTS_SQL to pick one.")
	else:print(f"NOTE: no course column found in {GALLEY}. Set COURSE_JOIN_SQL, COURSE_WHERE_SQL and COURSE_EXISTS_SQL to filter by course.")
def course_filter_active():_configure_course_filter();return bool(COURSE_WHERE_SQL)
def course_exists(course_id):
	global _course_note_shown;_configure_course_filter()
	if not COURSE_EXISTS_SQL:
		if not _course_note_shown:print('NOTE: course check not configured; course ids were not verified.');_course_note_shown=True
		return True
	with closing(connect())as conn:return conn.cursor().execute(COURSE_EXISTS_SQL,course_id).fetchone()[0]>0
def missing_subjects(codes):
	codes,found=sorted(set(codes)),set()
	with closing(connect())as conn:
		cur=conn.cursor()
		for i in range(0,len(codes),1000):chunk=codes[i:i+1000];q='SELECT SUBJ_CODE FROM '+T('GenTMSubject')+' WHERE SUBJ_CODE IN (%s)'%','.join('?'*len(chunk));found|={str(r[0]).strip()for r in cur.execute(q,*chunk).fetchall()}
	return[c for c in codes if c not in found]
def fetch_groups():
	"""Returns {subject_code: group_id}. A subject listed in several active groups links those groups
	(they are treated as one group, with a printed warning) instead of stopping the run."""
	pairs=[]
	with closing(connect())as conn:
		for(group_id,code)in conn.cursor().execute(GROUPS_SQL).fetchall():pairs.append((group_id,str(code).strip()))
	parent={}
	def find(x):
		while parent.setdefault(x,x)!=x:parent[x]=parent[parent[x]];x=parent[x]
		return x
	first={};linked={}
	for(g,code)in pairs:
		if code in first and first[code]!=g:
			linked.setdefault(code,{first[code]}).add(g)
			a,b=find(first[code]),find(g)
			if a!=b:parent[max(a,b)]=min(a,b)
		else:first[code]=g
		find(g)
	if linked:
		print(f"WARNING: {len(linked)} subject(s) appear in more than one active group, so those groups were merged into one slot group:")
		for(code,gs)in sorted(linked.items())[:10]:print(f"  {code}: groups {sorted(gs)}")
		if len(linked)>10:print(f"  ... and {len(linked)-10} more. Fix the mapping data if this is not intended.")
	return{code:find(g)for(code,g)in first.items()}
def find_courses(text):
	"""Matches what a user may type: cour_id, cour_code, or part of the course name. Returns [(cour_id, cour_code, name)]."""
	text=str(text).strip()
	if not text:return[]
	with closing(connect())as conn:
		cur=conn.cursor();t=T('GenTMCourse')
		rows=cur.execute("SELECT cour_id, cour_code, display_name FROM "+t+" WHERE CAST(cour_id AS VARCHAR(30)) = ? OR LTRIM(RTRIM(cour_code)) = ?",text,text).fetchall()
		if not rows:rows=cur.execute("SELECT cour_id, cour_code, display_name FROM "+t+" WHERE display_name LIKE ?",'%'+text+'%').fetchall()
	return[(r[0],str(r[1]or'').strip(),str(r[2]or'').strip())for r in rows]
def course_cycles(course_ids):
	"""Cycles that have usable enrollments (confirmed, registered, not debarred, active student) for the given cour_ids, latest first.
	Returns [{'inst_id','proc_year','proc_sem','students','subjects','courses'}]; 'courses' = how many of the given courses appear in it."""
	ids=list(course_ids)
	if not ids:return[]
	sql=("SELECT g.Inst_Id, g.proc_year, g.proc_sem, COUNT(DISTINCT g.roll_no), COUNT(DISTINCT g.subj_code), COUNT(DISTINCT st.cour_id)\n"
		"FROM "+GALLEY+" g\n"+COURSE_JOIN_SQL.strip()+"\n"
		"WHERE st.cour_id IN ("+','.join('?'*len(ids))+")\n  "+_reg_sql()+"\n  AND g.confirm_tag = 'Y'\n  AND g.debar_type IS NULL"+_status_sql()+"\n"
		"GROUP BY g.Inst_Id, g.proc_year, g.proc_sem\nORDER BY g.proc_year DESC, g.proc_sem DESC, g.Inst_Id")
	with closing(connect())as conn:rows=conn.cursor().execute(sql,*ids).fetchall()
	return[{'inst_id':r[0],'proc_year':r[1],'proc_sem':r[2],'students':r[3],'subjects':r[4],'courses':r[5]}for r in rows]
def fetch_enrollments(proc_yy,proc_sem,course_ids,inst_id=None):
	inst=inst_id if inst_id is not None else INST_ID
	if inst is None:raise ValueError('Institute (Inst_Id) is not known: pass inst_id or set INST_ID in .env.')
	_configure_course_filter();sql=ENROLLMENT_SQL.format(join=COURSE_JOIN_SQL,where=COURSE_WHERE_SQL,reg=_reg_sql())
	if COURSE_WHERE_SQL:runs=list(course_ids)
	else:print('WARNING: course filter is NOT configured, so the course id(s) you typed are IGNORED.\n         Using ALL confirmed students for this year/semester.');runs=[None]
	seen,out=set(),[]
	with closing(connect())as conn:
		cur=conn.cursor()
		for cid in runs:
			params=[inst,proc_yy,proc_sem]+([cid]if cid is not None else[])
			for r in cur.execute(sql,*params).fetchall():
				try:attempt=int(r[2])if r[2]is not None else None
				except(TypeError,ValueError):attempt=None
				row={'roll_no':str(r[0]).strip(),'subj_code':str(r[1]).strip(),'attempt_no':attempt,'ilrs_tag':r[3],'course_id':cid};key=row['roll_no'],row['subj_code'],row['attempt_no']
				if key not in seen:seen.add(key);out.append(row)
	if not out:raise ValueError(f"No enrollments found in {GALLEY} for proc_year={proc_yy}, proc_sem={proc_sem}, course(s) {", ".join(course_ids)}.")
	missing=missing_subjects(r['subj_code']for r in out)
	if missing:print(f"WARNING: {len(missing)} subject code(s) in Galley are not in GenTMSubject: {missing[:10]}")
	return out
def _columns(cur,table):cur.execute('\n        SELECT c.name, c.is_nullable, c.is_identity, c.is_computed,\n               CASE WHEN dc.object_id IS NULL THEN 0 ELSE 1 END AS has_default,\n               t.name AS type_name\n        FROM sys.columns c\n        JOIN sys.types t ON c.user_type_id = t.user_type_id\n        LEFT JOIN sys.default_constraints dc\n               ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id\n        WHERE c.object_id = OBJECT_ID(?)\n        ',table);return{r.name.lower():{'name':r.name,'nullable':bool(r.is_nullable),'identity':bool(r.is_identity),'computed':bool(r.is_computed),'has_default':bool(r.has_default),'type':r.type_name}for r in cur.fetchall()}
def write_timetable(rows,proc_yy,proc_sem,inst_id=None):
	inst=inst_id if inst_id is not None else INST_ID
	conn=connect()
	try:
		cur=conn.cursor();cols=_columns(cur,EXAM_DATE)
		if not cols:raise ValueError(f"Table {EXAM_DATE} not found")
		base=['proc_yy','proc_sem','subj_code','exam_date','exam_session','session_order'];absent=[c for c in base if c not in cols]
		if absent:raise ValueError(f"{EXAM_DATE} is missing expected column(s): {absent}")
		use_inst='inst_id'in cols
		if use_inst and inst is None:raise ValueError('Institute (Inst_Id) is not known, nothing written.')
		handled=set(base)|({'inst_id'}if use_inst else set());unfilled=[v['name']for(k,v)in cols.items()if k not in handled and not v['nullable']and not v['identity']and not v['computed']and not v['has_default']and v['type']not in('timestamp','rowversion')]
		if unfilled:raise ValueError(f"{EXAM_DATE} has required column(s) this script does not fill: {unfilled}")
		name={k:v['name']for(k,v)in cols.items()};lead_cols=['inst_id']if use_inst else[];lead_vals=[inst]if use_inst else[];del_cols=lead_cols+['proc_yy','proc_sem','subj_code'];del_sql=f"DELETE FROM {EXAM_DATE} WHERE "+' AND '.join(f"[{name[c]}] = ?"for c in del_cols);ins_cols=lead_cols+base;ins_sql=f"INSERT INTO {EXAM_DATE} ("+', '.join(f"[{name[c]}]"for c in ins_cols)+') VALUES ('+', '.join('?'for _ in ins_cols)+')';del_params,ins_params=[],[]
		for r in rows:
			if any(r.get(k)in(None,'')for k in('subj_code','exam_date','exam_session','session_order')):raise ValueError(f"Incomplete timetable row, nothing written: {r}")
			exam_date=datetime.strptime(r['exam_date'],'%Y-%m-%d');del_params.append(tuple(lead_vals+[proc_yy,proc_sem,r['subj_code']]));ins_params.append(tuple(lead_vals+[proc_yy,proc_sem,r['subj_code'],exam_date,r['exam_session'],r['session_order']]))
		cur.executemany(del_sql,del_params);cur.executemany(ins_sql,ins_params);conn.commit();return len(ins_params)
	except Exception:conn.rollback();raise
	finally:conn.close()
def self_test():
	try:
		with closing(connect())as conn:
			cur=conn.cursor();print('Connected to database:',cur.execute('SELECT DB_NAME()').fetchone()[0])
			for t in(T('GenTMSubject'),GALLEY,T('GenExmTxExamSubj_Group'),T('GenExmTxExamSubjGrp_Mapping'),T('GenTMCourse'),T('SISTMSTUD'),EXAM_DATE):
				try:n=cur.execute('SELECT COUNT(*) FROM '+t).fetchone()[0];print(f"  {t}: {n} rows")
				except Exception as e:print(f"  {t}: ERROR {e}")
			print('\nTerms available in Galley (confirmed, not debarred):')
			for r in cur.execute("SELECT Inst_Id, proc_year, proc_sem, COUNT(*) FROM "+GALLEY+" WHERE confirm_tag = 'Y' AND debar_type IS NULL GROUP BY Inst_Id, proc_year, proc_sem ORDER BY proc_year DESC, proc_sem DESC, Inst_Id").fetchall():print(f"  Inst_Id={r[0]} proc_year={r[1]} proc_sem={r[2]}  rows={r[3]}")
			print(f"\n{EXAM_DATE} columns:")
			for(k,v)in _columns(cur,EXAM_DATE).items():
				flags=[]
				if not v['nullable']:flags.append('NOT NULL')
				if v['identity']:flags.append('identity')
				if v['has_default']:flags.append('default')
				print(f"  {v["name"]} ({v["type"]}) {" ".join(flags)}")
		_configure_course_filter()
	except Exception as e:print('FAILED:',e)
if __name__=='__main__':self_test()