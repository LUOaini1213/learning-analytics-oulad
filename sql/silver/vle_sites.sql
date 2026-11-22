select id_site, code_module, code_presentation, activity_type, week_from, week_to, n_versions,
       _batch_week as last_batch_week
from (
    select *, row_number() over (partition by id_site order by _batch_week desc, _source_row desc) as _rk,
           count(*) over (partition by id_site) as n_versions
    from stage.vle_sites
    where _reject_reason is null
) latest
where _rk = 1
