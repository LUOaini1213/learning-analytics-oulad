select
    trim(r.code_module) as code_module,
    trim(r.code_presentation) as code_presentation,
    try_cast(nullif(trim(r.id_student), '?') as integer) as id_student,
    try_cast(nullif(trim(r.date_registration), '?') as integer) as date_registration,
    try_cast(nullif(trim(r.date_unregistration), '?') as integer) as date_unregistration,
    r._batch_week, r._source_file, r._source_row,
    concat_ws('|', r.code_module, r.code_presentation, r.id_student, r.date_registration, r.date_unregistration) as _raw,
    case
        when try_cast(nullif(trim(r.id_student), '?') as integer) is null then 'missing_key'
        when (nullif(trim(r.date_registration), '?') is not null and try_cast(nullif(trim(r.date_registration), '?') as integer) is null) or (nullif(trim(r.date_unregistration), '?') is not null and try_cast(nullif(trim(r.date_unregistration), '?') as integer) is null) then 'bad_date'
        when try_cast(nullif(trim(r.date_registration), '?') as integer) not between -400 and 400 then 'date_out_of_range'
        when try_cast(nullif(trim(r.date_unregistration), '?') as integer) not between -400 and 500 then 'date_out_of_range'
        when c.code_module is null then 'orphan_module_presentation'
    end as _reject_reason
from bronze.student_registration r
left join silver.courses c
    on c.code_module = trim(r.code_module) and c.code_presentation = trim(r.code_presentation)
