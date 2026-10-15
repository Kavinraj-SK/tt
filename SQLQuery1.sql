EXEC [DOTE].[GenPrIUExamTimetable_final]
    @ProcYear  = 2026,
    @ProcSem   = 1,
    @ILRSTag   = null,
    @SubjType  = 'T',
    @StartDate = '2026-10-10',
    @EndDate   = '2026-11-06',
    @Interval  = 0,
    @CourNature=null;
-------------------------------------------------
SELECT * FROM [test].GenExmTxGalley;
SELECT * FROM [test].GenTMSubject;

SELECT * FROM [test].GenExmTxExamDate;--INSERT
SELECT * FROM  [test].GenExmTxExamSubjGrp_Mapping;--TRANSACTION GROUP MAPPING 
SELECT * FROM [test].GenExmTxExamSubj_Group;--GROUP MASTER
select * from test.Gentmcourse;

-----------------------------------------------------------------------------------------------------------
select * from GenExmTxExamDate order by exam_date desc;
-----------------------------------------------------------------
select * from GenTmCourse;

SELECT TABLE_SCHEMA, TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_NAME IN ('GenExmTxGalley','GenTMSubject','GenExmTxExamDate',
                     'GenExmTxExamSubjGrp_Mapping','GenExmTxExamSubj_Group',
                     'GenTmCourse','SISTMSTUD','GenTmHolidayDet')
ORDER BY TABLE_NAME;
--------------------------
SELECT * INTO test.GenExmTxExamSubj_Group FROM dbo.GenExmTxExamSubj_Group;


---------

INSERT INTO test.GenExmTxExamSubjGrp_Mapping SELECT * FROM dbo.GenExmTxExamSubjGrp_Mapping;











