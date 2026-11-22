-- An exam without a date is sat at the end of the last presentation week (dataset documentation),
-- so due_day_effective falls back to the presentation length. due_day keeps the published value.
select s.code_module, s.code_presentation, s.id_assessment, s.assessment_type, s.due_day,
       coalesce(s.due_day, c.module_presentation_length) as due_day_effective,
       s.due_day is null as due_day_missing,
       s.weight, s.n_versions, s._batch_week as last_batch_week
from (
    select *, row_number() over (partition by id_assessment order by _batch_week desc, _source_row desc) as _rk,
           count(*) over (partition by id_assessment) as n_versions
    from stage.assessments
    where _reject_reason is null
) s
join silver.courses c on c.code_module = s.code_module and c.code_presentation = s.code_presentation
where s._rk = 1
