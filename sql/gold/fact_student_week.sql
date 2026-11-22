-- Grain: student-presentation x week. Recomputed only for run.affected_weeks.
select substr(sha256(concat((select pseudonym_salt from run.settings), '|', code_module, '|', code_presentation, '|', cast(id_student as varchar))), 1, 12) as sp_key,
       concat(code_module, '-', code_presentation) as mp_key,
       cast(sum(sum_click) as bigint) as clicks,
       count(distinct date) as active_days,
       count(distinct id_site) as sites_visited,
       min(date) as first_active_day,
       max(date) as last_active_day,
       event_week as week_index
from silver.vle_daily
where event_week in (select week from run.affected_weeks)
group by code_module, code_presentation, id_student, event_week
