-- The published click table has no primary key: a student can have several rows for the same material
-- on the same day. Rows are therefore never de-duplicated on content; silver.vle_daily adds them up.
select
    trim(v.code_module) as code_module,
    trim(v.code_presentation) as code_presentation,
    try_cast(nullif(trim(v.id_student), '?') as integer) as id_student,
    try_cast(nullif(trim(v.id_site), '?') as integer) as id_site,
    try_cast(nullif(trim(v.date), '?') as integer) as date,
    try_cast(nullif(trim(v.sum_click), '?') as integer) as sum_click,
    cast(floor(try_cast(nullif(trim(v.date), '?') as integer) / 7.0) as integer) as event_week,
    v._batch_week, v._source_file, v._source_row,
    concat_ws('|', v.code_module, v.code_presentation, v.id_student, v.id_site, v.date, v.sum_click) as _raw,
    case
        when try_cast(nullif(trim(v.id_student), '?') as integer) is null or try_cast(nullif(trim(v.id_site), '?') as integer) is null then 'missing_key'
        when try_cast(nullif(trim(v.date), '?') as integer) is null then 'bad_date'
        when try_cast(nullif(trim(v.date), '?') as integer) not between -60 and 400 then 'date_out_of_range'
        when try_cast(nullif(trim(v.sum_click), '?') as integer) is null then 'bad_click_count'
        when try_cast(nullif(trim(v.sum_click), '?') as integer) < 1 then 'click_count_out_of_range'
        when s.id_site is null then 'orphan_site'
        when r.id_student is null then 'orphan_student'
    end as _reject_reason
from bronze.student_vle v
left join silver.vle_sites s
    on s.id_site = try_cast(nullif(trim(v.id_site), '?') as integer) and s.code_module = trim(v.code_module)
   and s.code_presentation = trim(v.code_presentation)
left join silver.student_registration r
    on r.code_module = trim(v.code_module) and r.code_presentation = trim(v.code_presentation)
   and r.id_student = try_cast(nullif(trim(v.id_student), '?') as integer)
