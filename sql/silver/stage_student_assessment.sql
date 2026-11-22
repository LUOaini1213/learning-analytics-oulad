select
    try_cast(nullif(trim(s.id_assessment), '?') as integer) as id_assessment,
    try_cast(nullif(trim(s.id_student), '?') as integer) as id_student,
    try_cast(nullif(trim(s.date_submitted), '?') as integer) as date_submitted,
    try_cast(nullif(trim(s.is_banked), '?') as integer) as is_banked,
    try_cast(nullif(trim(s.score), '?') as double) as score,
    a.code_module, a.code_presentation,
    s._batch_week, s._source_file, s._source_row,
    concat_ws('|', s.id_assessment, s.id_student, s.date_submitted, s.is_banked, s.score) as _raw,
    case
        when try_cast(nullif(trim(s.id_assessment), '?') as integer) is null or try_cast(nullif(trim(s.id_student), '?') as integer) is null then 'missing_key'
        when (nullif(trim(s.date_submitted), '?') is not null and try_cast(nullif(trim(s.date_submitted), '?') as integer) is null) then 'bad_date'
        when try_cast(nullif(trim(s.is_banked), '?') as integer) is null or try_cast(nullif(trim(s.is_banked), '?') as integer) not in (0, 1) then 'bad_is_banked'
        when nullif(trim(s.score), '?') is not null and try_cast(trim(s.score) as double) is null then 'bad_score'
        when try_cast(nullif(trim(s.score), '?') as double) not between 0 and 100 then 'score_out_of_range'
        when a.id_assessment is null then 'orphan_assessment'
        when r.id_student is null then 'orphan_student'
    end as _reject_reason
from bronze.student_assessment s
left join silver.assessments a on a.id_assessment = try_cast(nullif(trim(s.id_assessment), '?') as integer)
left join silver.student_registration r
    on r.code_module = a.code_module and r.code_presentation = a.code_presentation
   and r.id_student = try_cast(nullif(trim(s.id_student), '?') as integer)
