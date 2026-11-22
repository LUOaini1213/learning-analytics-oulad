-- Grain: one submitted assessment per student. days_late is null for banked results (carried over from an
-- earlier presentation, so the recorded submission day is not behaviour in this one).
select substr(sha256(concat((select pseudonym_salt from run.settings), '|', s.code_module, '|', s.code_presentation, '|', cast(s.id_student as varchar))), 1, 12) as sp_key,
       concat(s.code_module, '-', s.code_presentation) as mp_key,
       s.id_assessment as assessment_key,
       a.assessment_type,
       s.date_submitted,
       cast(floor(s.date_submitted / 7.0) as integer) as submitted_week,
       s.is_banked,
       s.score,
       case when s.is_banked = 0 then s.date_submitted - a.due_day_effective end as days_late
from silver.student_assessment s
join silver.assessments a on a.id_assessment = s.id_assessment
