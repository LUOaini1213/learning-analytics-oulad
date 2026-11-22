select
    try_cast(nullif(trim(v.id_site), '?') as integer) as id_site,
    trim(v.code_module) as code_module,
    trim(v.code_presentation) as code_presentation,
    trim(v.activity_type) as activity_type,
    try_cast(nullif(trim(v.week_from), '?') as integer) as week_from,
    try_cast(nullif(trim(v.week_to), '?') as integer) as week_to,
    v._batch_week, v._source_file, v._source_row,
    concat_ws('|', v.id_site, v.code_module, v.code_presentation, v.activity_type, v.week_from, v.week_to) as _raw,
    case
        when try_cast(nullif(trim(v.id_site), '?') as integer) is null then 'missing_key'
        when nullif(trim(v.activity_type), '') is null then 'missing_activity_type'
        when (nullif(trim(v.week_from), '?') is not null and try_cast(nullif(trim(v.week_from), '?') as integer) is null) or (nullif(trim(v.week_to), '?') is not null and try_cast(nullif(trim(v.week_to), '?') as integer) is null) then 'bad_week'
        when c.code_module is null then 'orphan_module_presentation'
    end as _reject_reason
from bronze.vle v
left join silver.courses c
    on c.code_module = trim(v.code_module) and c.code_presentation = trim(v.code_presentation)
