-- Latest accepted version of each student's record: the final result arrives as a later version.
select code_module, code_presentation, id_student, gender, region, highest_education, imd_band, age_band,
       num_of_prev_attempts, studied_credits, disability, final_result, n_versions, _batch_week as last_batch_week
from (
    select *, row_number() over (partition by code_module, code_presentation, id_student
                                 order by _batch_week desc, _source_row desc) as _rk,
           count(*) over (partition by code_module, code_presentation, id_student) as n_versions
    from stage.student_info
    where _reject_reason is null
) latest
where _rk = 1
