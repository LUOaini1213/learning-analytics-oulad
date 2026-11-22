-- College management: aggregates only. Any count of students below 5 is withheld (shown as null), and so
-- is any rate that would reveal such a count.
--   primary suppression        a cell with fewer than 5 students, or an at-risk count (or its complement)
--                              below 5, is withheld;
--   complementary suppression  if exactly one cell in a breakdown is withheld, the smallest remaining cell
--                              is withheld too, so the hidden value cannot be recovered from the total.
create or replace view access.mgmt_outcomes as
with base as (
    select d.mp_key,
           count(*) as n_students,
           sum(case when d.final_result = 'Withdrawn' then 1 else 0 end) as n_withdrawn,
           sum(case when d.final_result = 'Fail' then 1 else 0 end) as n_fail,
           sum(case when d.final_result in ('Pass', 'Distinction') then 1 else 0 end) as n_pass,
           sum(case when d.final_result is null then 1 else 0 end) as n_no_result
    from gold.dim_student_presentation d
    group by d.mp_key
)
select b.mp_key,
       case when b.n_students >= 5 then b.n_students end as n_students,
       case when b.n_students >= 5 and b.n_withdrawn >= 5 and b.n_students - b.n_withdrawn >= 5 then b.n_withdrawn end as n_withdrawn,
       case when b.n_students >= 5 and b.n_fail >= 5 and b.n_students - b.n_fail >= 5 then b.n_fail end as n_fail,
       case when b.n_students >= 5 and b.n_pass >= 5 and b.n_students - b.n_pass >= 5 then b.n_pass end as n_pass,
       b.n_students < 5 as suppressed
from base b
cross join run.session s
where s.app_role in ('management', 'data_engineer');

create or replace view access.mgmt_outcomes_by_group as
with cells as (
    select d.mp_key, g.dimension,
           case g.dimension when 'gender' then d.gender
                            when 'disability' then d.disability
                            when 'age_band' then d.age_band
                            when 'imd_band' then coalesce(d.imd_band, 'Not recorded')
                            when 'highest_education' then d.highest_education end as band,
           d.at_risk
    from gold.dim_student_presentation d
    cross join (select 'gender' as dimension union all select 'disability' union all select 'age_band'
                union all select 'imd_band' union all select 'highest_education') g
),
agg as (
    select mp_key, dimension, band, count(*) as n_students, sum(at_risk) as n_at_risk,
           count(at_risk) as n_with_result
    from cells
    group by mp_key, dimension, band
),
flagged as (
    select a.*,
           a.n_students < 5 as primary_n,
           sum(case when a.n_students < 5 then 1 else 0 end) over (partition by a.mp_key, a.dimension) as n_primary,
           row_number() over (partition by a.mp_key, a.dimension
                              order by case when a.n_students < 5 then 1 else 0 end, a.n_students, a.band) as rank_among_shown
    from agg a
),
decided as (
    select f.*,
           (f.primary_n or (f.n_primary = 1 and f.rank_among_shown = 1)) as hide_n
    from flagged f
)
select d.mp_key, d.dimension, d.band,
       case when not d.hide_n then d.n_students end as n_students,
       case when not d.hide_n and d.n_with_result = d.n_students and d.n_at_risk >= 5
                 and d.n_students - d.n_at_risk >= 5 then d.n_at_risk end as n_at_risk,
       d.hide_n as suppressed,
       case when d.primary_n then 'primary' when d.hide_n then 'complementary' end as suppression_reason
from decided d
cross join run.session s
where s.app_role in ('management', 'data_engineer');

create or replace view access.mgmt_weekly_engagement as
with base as (
    select f.mp_key, f.week_index, count(*) as n_active_students, sum(f.clicks) as clicks
    from gold.fact_student_week f
    group by f.mp_key, f.week_index
)
select b.mp_key, b.week_index,
       case when b.n_active_students >= 5 then b.n_active_students end as n_active_students,
       case when b.n_active_students >= 5 then b.clicks end as clicks,
       b.n_active_students < 5 as suppressed
from base b
cross join run.session s
where s.app_role in ('management', 'data_engineer')
