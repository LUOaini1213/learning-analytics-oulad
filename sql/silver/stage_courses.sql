-- One row per bronze row: typed columns plus _reject_reason (null = accepted).
select
    trim(code_module) as code_module,
    trim(code_presentation) as code_presentation,
    try_cast(nullif(trim(module_presentation_length), '?') as integer) as module_presentation_length,
    _batch_week, _source_file, _source_row,
    concat_ws('|', code_module, code_presentation, module_presentation_length) as _raw,
    case
        when nullif(trim(code_module), '') is null or nullif(trim(code_presentation), '') is null then 'missing_key'
        when not regexp_matches(trim(code_presentation), '^20[0-9][0-9][BJ]$') then 'bad_presentation_code'
        when try_cast(nullif(trim(module_presentation_length), '?') as integer) is null then 'bad_length'
        when try_cast(nullif(trim(module_presentation_length), '?') as integer) not between 1 and 400 then 'length_out_of_range'
    end as _reject_reason
from bronze.courses
