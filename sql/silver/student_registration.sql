-- Current state of each registration: the latest delivery wins (upsert by key).
select code_module, code_presentation, id_student, date_registration, date_unregistration, n_versions,
       first_batch_week, _batch_week as last_batch_week
from (
    select *, row_number() over (partition by code_module, code_presentation, id_student
                                 order by _batch_week desc, _source_row desc) as _rk,
           count(*) over (partition by code_module, code_presentation, id_student) as n_versions,
           min(_batch_week) over (partition by code_module, code_presentation, id_student) as first_batch_week
    from stage.student_registration
    where _reject_reason is null
) latest
where _rk = 1
