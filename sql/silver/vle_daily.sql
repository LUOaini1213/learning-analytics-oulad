-- One row per student, material and day. Only the weeks listed in run.affected_weeks are recomputed;
-- the runner overwrites exactly those partitions, which makes re-runs idempotent and absorbs late rows.
select code_module, code_presentation, id_student, id_site, date,
       cast(sum(sum_click) as bigint) as sum_click,
       count(*) as n_source_rows,
       min(_batch_week) as first_batch_week,
       max(_batch_week) as last_batch_week,
       event_week
from stage.student_vle
where _reject_reason is null
  and event_week in (select week from run.affected_weeks)
group by code_module, code_presentation, id_student, id_site, date, event_week
