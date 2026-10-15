import csv,json,os,sys
from collections import defaultdict
from datetime import date,datetime,timedelta
from itertools import combinations
from dotenv import load_dotenv
from ortools.sat.python import cp_model
HERE=os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE,'.env'))
GROUPS_FILE=os.path.join(HERE,'sample_groups.json')
ENROLL_FILE=os.path.join(HERE,'sample_enrollments.csv')
COURSES_FILE=os.path.join(HERE,'sample_courses.json')
OUT_FILE=os.path.join(HERE,'timetable.json')
DEFAULT_YY=int(os.getenv('PROC_YY')or 2025)
DEFAULT_SEM=int(os.getenv('PROC_SEM')or 1)
SESSIONS=['F','A']
SKIP_WEEKDAYS={6}
SOLVER_SECONDS=int(os.getenv('SOLVER_SECONDS')or 30)
USE_DB=os.getenv('USE_DB','true').strip().lower()in('1','true','yes','y')
class CourseNotFoundError(Exception):pass
def is_ra(row):
	try:return int(row['attempt_no'])>1
	except(TypeError,ValueError):return False
def ask_date(prompt):
	while True:
		try:return datetime.strptime(input(prompt).strip(),'%Y-%m-%d').date()
		except ValueError:print('  Please enter the date as YYYY-MM-DD.')
def ask_int(prompt,minimum=0,default=None):
	while True:
		raw=input(prompt).strip()
		if raw==''and default is not None:return default
		try:
			v=int(raw)
			if v>=minimum:return v
		except ValueError:pass
		print(f"  Please enter a whole number >= {minimum}.")
def ask_yes_no(prompt):
	while True:
		a=input(prompt).strip().lower()
		if a in('y','yes'):return True
		if a in('n','no'):return False
		print('  Please type y or n.')
def ask_subject_gaps(subjects,default_gap):
	gaps={c:default_gap for c in subjects};ans=input(f"\nSet a different gap for individual subjects? Default is {default_gap} day(s) for all. (y/n): ")
	if ans.strip().lower()not in('y','yes'):return gaps
	print("For each subject enter the minimum days between a student's previous exam and this subject.");print('Press Enter to keep the default.\n')
	for c in sorted(subjects):
		while True:
			raw=input(f"  Gap days before {c} [{default_gap}]: ").strip()
			if raw=='':break
			if raw.isdigit():gaps[c]=int(raw);break
			print('    Please enter a whole number, or press Enter.')
	return gaps
def course_exists(course_id):
	if USE_DB:import db;return db.course_exists(course_id)
	with open(COURSES_FILE)as f:return course_id in json.load(f)
def ask_choice(prompt,low,high):
	while True:
		raw=input(prompt).strip()
		if raw.isdigit()and low<=int(raw)<=high:return int(raw)
		print(f"  Please enter a number from {low} to {high}.")
def resolve_courses(tokens):
	"""Each token may be a cour_id, a course code, or part of the course name."""
	import db
	ids,seen=[],set()
	for t in tokens:
		found=db.find_courses(t)
		if not found:raise CourseNotFoundError(f"Course '{t}' not found in the database (tried course id, code and name).")
		if len(found)>1:
			print(f"'{t}' matches several courses:")
			for(i,(cid,code,name))in enumerate(found,1):print(f"  {i}) id {cid}  {code}  {name}")
			found=[found[ask_choice('Pick one (number): ',1,len(found))-1]]
		cid,code,name=found[0];cid=str(cid)
		if cid not in seen:seen.add(cid);ids.append(cid)
		print(f"  Course: id {cid}  {code}  {name}")
	return ids
def choose_cycle(cycles,n_courses):
	"""cycles are latest-first. Enter 0 to take the latest."""
	def label(c):return f"{c['proc_year']} sem {c['proc_sem']} (institute {c['inst_id']}): {c['students']} students, {c['subjects']} subjects, {c['courses']} of {n_courses} course(s)"
	if len(cycles)==1:c=cycles[0];print('Using cycle '+label(c))
	else:
		print('\nCycles with exam enrollments for these course(s), latest first:')
		for(i,c)in enumerate(cycles,1):print(f"  {i}) {label(c)}")
		k=ask_choice('Cycle number (0 = latest): ',0,len(cycles));c=cycles[0 if k==0 else k-1];print('Using cycle '+label(c))
	if c['courses']<n_courses and not ask_yes_no(f"  Only {c['courses']} of {n_courses} course(s) have enrollments in this cycle. Continue with those only? (y/n): "):raise ValueError('Cancelled: pick another cycle or check the course list.')
	return c
def get_inputs():
	"""Returns proc_yy, proc_sem, start, end, gap, course_ids, inst_id."""
	inst_id=None
	if USE_DB:
		import db
		raw=input('Course(s) - id, code or name; separate several with commas: ');tokens=list(dict.fromkeys(c.strip()for c in raw.split(',')if c.strip()))
		if not tokens:raise CourseNotFoundError('No course entered.')
		course_ids=resolve_courses(tokens);cycles=db.course_cycles(course_ids)
		if not cycles:raise ValueError('No usable enrollments (confirmed, registered, not debarred, active student) were found for these course(s) in any cycle.')
		c=choose_cycle(cycles,len(course_ids));proc_yy,proc_sem,inst_id=int(c['proc_year']),int(c['proc_sem']),c['inst_id']
	else:proc_yy=ask_int(f"Process year [{DEFAULT_YY}]: ",minimum=1900,default=DEFAULT_YY);proc_sem=ask_int(f"Process semester [{DEFAULT_SEM}]: ",minimum=1,default=DEFAULT_SEM)
	start=ask_date('Start date (YYYY-MM-DD): ')
	while True:
		end=ask_date('End date   (YYYY-MM-DD): ')
		if end>=start:break
		print('  End date must be on or after the start date.')
	gap=ask_int("Default gap between a student's exams in days (you can change it per subject next)\n  (0 = same day allowed in a different session, 1 = different days,\n   2 = at least one free day between exams): ")
	if not USE_DB:
		raw=input('Course id(s), separated by commas: ');course_ids=list(dict.fromkeys(c.strip()for c in raw.split(',')if c.strip()))
		if not course_ids:raise CourseNotFoundError('No course id entered.')
		missing=[c for c in course_ids if not course_exists(c)]
		if missing:raise CourseNotFoundError(f"Course id(s) not found: {', '.join(missing)}")
	return proc_yy,proc_sem,start,end,gap,course_ids,inst_id
def load_groups(path):
	with open(path)as f:data=json.load(f)
	subj_to_group={}
	for g in data['groups']:
		if not g.get('active',True):continue
		for s in g['subjects']:
			code=s['code']
			if code in subj_to_group and subj_to_group[code]!=g['group_id']:raise ValueError(f"Subject {code} appears in two groups")
			subj_to_group[code]=g['group_id']
	return subj_to_group
def load_enrollments(path,course_ids):
	with open(path,newline='')as f:rows=[r for r in csv.DictReader(f)if r.get('course_id')in course_ids]
	if not rows:raise ValueError(f"No enrollments found for course(s): {", ".join(course_ids)}.")
	return rows
def build_slots(start,end):
	slots,d=[],start
	while d<=end:
		if d.weekday()not in SKIP_WEEKDAYS:slots+=[{'date':d.isoformat(),'session':s}for s in SESSIONS]
		d+=timedelta(days=1)
	if not slots:raise ValueError('No exam slots in that date range.')
	return slots
def solve(subj_to_group,enrollments,slots,gaps,time_limit=SOLVER_SECONDS):
	def unit_of(code):return f"G{subj_to_group[code]}"if code in subj_to_group else code
	cur,ra=defaultdict(set),defaultdict(set);unit_codes=defaultdict(set)
	for r in enrollments:u=unit_of(r['subj_code']);unit_codes[u].add(r['subj_code']);(ra if is_ra(r)else cur)[r['roll_no']].add(u)
	ugap={u:max(gaps.get(c,0)for c in codes)for(u,codes)in unit_codes.items()};any_gap=any(g>0 for g in ugap.values());students=set(cur)|set(ra);warnings=[f"{s}: same paper/group as current and RA: {sorted(cur[s]&ra[s])}"for s in students if cur[s]&ra[s]];n=len(slots);day_ords=[date.fromisoformat(s['date']).toordinal()for s in slots];model=cp_model.CpModel();slot={u:model.NewIntVar(0,n-1,u)for u in unit_codes};day={}
	if any_gap:
		for u in unit_codes:day[u]=model.NewIntVar(min(day_ords),max(day_ords),'d_'+u);model.AddElement(slot[u],day_ords,day[u])
	seen_sets,seen_pairs=set(),set()
	for roll in students:
		units=sorted(cur[roll]|ra[roll])
		if len(units)<2:continue
		if len(units)>n:raise ValueError(f"{roll} writes {len(units)} papers but only {n} slots exist")
		if not any_gap:
			key=frozenset(units)
			if key not in seen_sets:seen_sets.add(key);model.AddAllDifferent([slot[u]for u in units])
		else:
			for(a,b)in combinations(units,2):
				if(a,b)in seen_pairs:continue
				seen_pairs.add((a,b));a_first=model.NewBoolVar(f"{a}_{b}");model.Add(slot[a]<slot[b]).OnlyEnforceIf(a_first);model.Add(day[b]-day[a]>=ugap[b]).OnlyEnforceIf(a_first);model.Add(slot[b]<slot[a]).OnlyEnforceIf(a_first.Not());model.Add(day[a]-day[b]>=ugap[a]).OnlyEnforceIf(a_first.Not())
	last=model.NewIntVar(0,n-1,'last');model.AddMaxEquality(last,list(slot.values()));model.Minimize(last);solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=time_limit;solver.parameters.num_workers=8;status=solver.Solve(model)
	if status not in(cp_model.OPTIMAL,cp_model.FEASIBLE):return None,warnings,'INFEASIBLE: not enough slots for these rules. Extend the end date or reduce the gap days.'
	rows=[]
	for(u,v)in slot.items():
		s=solver.Value(v);order=sum(1 for x in slots[:s]if x['date']==slots[s]['date'])+1
		for code in sorted(unit_codes[u]):rows.append({'subj_code':code,'exam_date':slots[s]['date'],'exam_session':slots[s]['session'],'session_order':order})
	return rows,warnings,solver.StatusName(status)
def verify(rows,enrollments,gaps,subj_to_group):
	where={r['subj_code']:(r['exam_date'],r['exam_session'],r['session_order'])for r in rows};papers=defaultdict(set)
	for e in enrollments:papers[e['roll_no']].add(e['subj_code'])
	problems=[]
	for(roll,codes)in papers.items():
		for(a,b)in combinations(sorted(codes),2):
			if a in subj_to_group and b in subj_to_group and subj_to_group[a]==subj_to_group[b]:continue
			if where[a][:2]==where[b][:2]:problems.append((roll,a,b,'same slot'));continue
			first,later=sorted((a,b),key=lambda c:(where[c][0],where[c][2]));diff=date.fromisoformat(where[later][0]).toordinal()-date.fromisoformat(where[first][0]).toordinal()
			if diff<gaps.get(later,0):problems.append((roll,first,later,f"{later} needs {gaps.get(later,0)} day(s) gap, got {diff}"))
	return problems
def main():
	proc_yy,proc_sem,start,end,gap,course_ids,inst_id=get_inputs()
	if USE_DB:import db;groups=db.fetch_groups();enrollments=db.fetch_enrollments(proc_yy,proc_sem,course_ids,inst_id);filtered=db.course_filter_active()
	else:groups=load_groups(GROUPS_FILE);enrollments=load_enrollments(ENROLL_FILE,course_ids);filtered=True
	slots=build_slots(start,end)
	if groups:print(f"Groups: {len(set(groups.values()))} active group(s) covering {len(groups)} subject(s).")
	else:print('NOTE: no active subject groups found (GenExmTxExamSubjGrp_Mapping / GenExmTxExamSubj_Group have no usable rows). Every subject will be scheduled independently.')
	students={e['roll_no']for e in enrollments};subjects={e['subj_code']for e in enrollments};scope=f"for course(s) {", ".join(course_ids)}"if filtered else'(ALL students; course id NOT applied)';print(f"Enrollments: {len(enrollments)} rows, {len(students)} students, {len(subjects)} subjects {scope}.");gaps=ask_subject_gaps(subjects,gap);rows,warnings,status=solve(groups,enrollments,slots,gaps)
	for w in warnings:print('WARNING:',w)
	if rows is None:sys.exit(status)
	problems=verify(rows,enrollments,gaps,groups)
	if problems:sys.exit(f"Verification failed: {problems}")
	with open(OUT_FILE,'w')as f:json.dump({'proc_yy':proc_yy,'proc_sem':proc_sem,'inst_id':inst_id,'course_ids':course_ids,'start_date':start.isoformat(),'end_date':end.isoformat(),'default_gap_days':gap,'subject_gap_days':gaps,'rows':rows},f,indent=2)
	print(f"\n{status}: {len(rows)} subjects scheduled, 0 clashes -> {OUT_FILE}")
	for r in sorted(rows,key=lambda r:(r['exam_date'],r['session_order'])):print(r['exam_date'],r['exam_session'],r['subj_code'])
	if USE_DB:
		if ask_yes_no('\nWrite this timetable into GenExmTxExamDate? (y/n): '):import db;n=db.write_timetable(rows,proc_yy,proc_sem,inst_id);print(f"Saved {n} rows to GenExmTxExamDate.")
		else:print('Not written to the database.')
if __name__=='__main__':
	try:main()
	except(CourseNotFoundError,ValueError)as e:sys.exit(f"ERROR: {e}")
	except FileNotFoundError as e:sys.exit(f"ERROR: file not found: {e.filename}\n(Sample files are only needed when USE_DB=false in .env. Set USE_DB=true to use the database.)")
	except Exception as e:sys.exit(f"ERROR: {type(e).__name__}: {e}")