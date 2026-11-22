-- Latest accepted version of each module-presentation.
select code_module, code_presentation, module_presentation_length, n_versions, _batch_week as last_batch_week
from (
    select *, row_number() over (partition by code_module, code_presentation
                                 order by _batch_week desc, _source_row desc) as _rk,
           count(*) over (partition by code_module, code_presentation) as n_versions
    from stage.courses
    where _reject_reason is null
) latest
where _rk = 1
