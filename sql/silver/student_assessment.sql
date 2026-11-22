select id_assessment, id_student, code_module, code_presentation, date_submitted, is_banked, score, n_versions,
       _batch_week as last_batch_week
from (
    select *, row_number() over (partition by id_assessment, id_student
                                 order by _batch_week desc, _source_row desc) as _rk,
           count(*) over (partition by id_assessment, id_student) as n_versions
    from stage.student_assessment
    where _reject_reason is null
) latest
where _rk = 1
