EXEC [DOTE].[GenPrIUExamTimetable_final]
    @ProcYear  = 2026,
    @ProcSem   = 1,
    @ILRSTag   = null,
    @SubjType  = 'T',
    @StartDate = '2026-10-10',
    @EndDate   = '2026-11-06',
    @Interval  = 0,
    @CourNature=null;

SELECT * FROM GenExmTxGalley;
SELECT * FROM GenTMSubject;

SELECT * FROM GenExmTxExamDate;--INSERT
SELECT * FROM  GenExmTxExamSubjGrp_Mapping;--TRANSACTION GROUP MAPPING 
SELECT * FROM GenExmTxExamSubj_Group;--GROUP MASTER
-----------------------------------------------------------------------------------------------------------
select * from GenExmTxExamDate order by exam_date desc;

select * from GenExmTxGalley where debar_type not in ('0');
