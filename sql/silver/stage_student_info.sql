select
    trim(s.code_module) as code_module,
    trim(s.code_presentation) as code_presentation,
    try_cast(nullif(trim(s.id_student), '?') as integer) as id_student,
    trim(s.gender) as gender,
    trim(s.region) as region,
    trim(s.highest_education) as highest_education,
    -- the published file writes one band as '10-20' without the percent sign; '?' means not recorded
    case when trim(s.imd_band) = '10-20' then '10-20%' else nullif(trim(s.imd_band), '?') end as imd_band,
    trim(s.age_band) as age_band,
    try_cast(nullif(trim(s.num_of_prev_attempts), '?') as integer) as num_of_prev_attempts,
    try_cast(nullif(trim(s.studied_credits), '?') as integer) as studied_credits,
    trim(s.disability) as disability,
    nullif(trim(s.final_result), '?') as final_result,
    s._batch_week, s._source_file, s._source_row,
    concat_ws('|', s.code_module, s.code_presentation, s.id_student, s.gender, s.region, s.highest_education,
              s.imd_band, s.age_band, s.num_of_prev_attempts, s.studied_credits, s.disability, s.final_result) as _raw,
    case
        when try_cast(nullif(trim(s.id_student), '?') as integer) is null then 'missing_key'
        when trim(s.gender) not in ('M', 'F') or s.gender is null then 'bad_gender'
        when trim(s.disability) not in ('Y', 'N') or s.disability is null then 'bad_disability'
        when trim(s.age_band) not in ('0-35', '35-55', '55<=') or s.age_band is null then 'bad_age_band'
        when nullif(trim(s.final_result), '?') not in ('Pass', 'Fail', 'Withdrawn', 'Distinction') then 'bad_final_result'
        when try_cast(nullif(trim(s.num_of_prev_attempts), '?') as integer) is null or try_cast(nullif(trim(s.num_of_prev_attempts), '?') as integer) < 0 then 'bad_prev_attempts'
        when try_cast(nullif(trim(s.studied_credits), '?') as integer) is null or try_cast(nullif(trim(s.studied_credits), '?') as integer) < 0 then 'bad_credits'
        when c.code_module is null then 'orphan_module_presentation'
    end as _reject_reason
from bronze.student_info s
left join silver.courses c
    on c.code_module = trim(s.code_module) and c.code_presentation = trim(s.code_presentation)
