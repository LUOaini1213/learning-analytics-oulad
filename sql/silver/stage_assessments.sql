select
    trim(a.code_module) as code_module,
    trim(a.code_presentation) as code_presentation,
    try_cast(nullif(trim(a.id_assessment), '?') as integer) as id_assessment,
    trim(a.assessment_type) as assessment_type,
    try_cast(nullif(trim(a.date), '?') as integer) as due_day,
    try_cast(nullif(trim(a.weight), '?') as double) as weight,
    a._batch_week, a._source_file, a._source_row,
    concat_ws('|', a.code_module, a.code_presentation, a.id_assessment, a.assessment_type, a.date, a.weight) as _raw,
    case
        when try_cast(nullif(trim(a.id_assessment), '?') as integer) is null then 'missing_key'
        when trim(a.assessment_type) not in ('TMA', 'CMA', 'Exam') or a.assessment_type is null then 'bad_assessment_type'
        when (nullif(trim(a.date), '?') is not null and try_cast(nullif(trim(a.date), '?') as integer) is null) then 'bad_date'
        when try_cast(nullif(trim(a.weight), '?') as double) is null then 'bad_weight'
        when try_cast(nullif(trim(a.weight), '?') as double) not between 0 and 100 then 'weight_out_of_range'
        when c.code_module is null then 'orphan_module_presentation'
    end as _reject_reason
from bronze.assessments a
left join silver.courses c
    on c.code_module = trim(a.code_module) and c.code_presentation = trim(a.code_presentation)
