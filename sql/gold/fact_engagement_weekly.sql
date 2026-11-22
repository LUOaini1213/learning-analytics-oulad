-- Grain: student-presentation x week x activity type. Recomputed only for run.affected_weeks.
select substr(sha256(concat((select pseudonym_salt from run.settings), '|', v.code_module, '|', v.code_presentation, '|', cast(v.id_student as varchar))), 1, 12) as sp_key,
       concat(v.code_module, '-', v.code_presentation) as mp_key,
       s.activity_type,
       cast(sum(v.sum_click) as bigint) as clicks,
       count(distinct v.date) as active_days,
       v.event_week as week_index
from silver.vle_daily v
join silver.vle_sites s on s.id_site = v.id_site
where v.event_week in (select week from run.affected_weeks)
group by v.code_module, v.code_presentation, v.id_student, s.activity_type, v.event_week
