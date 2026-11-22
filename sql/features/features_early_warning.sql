-- Early-warning features, one row per student-presentation per cut-off day D (run.cutoffs, multiples of 7).
-- "As of D" means what was known at the end of day D-1:
--   * clicks from weeks that ended before D (week_index < D / 7);
--   * submissions dated before D; assessments count as due only if their due day is before D;
--   * the student had registered before D and had not unregistered before D.
-- date_unregistration and final_result are never used as features: the first is the withdrawal label in
-- disguise, the second is the label. final_result and at_risk are carried only as the target.
with pop as (
    select c.cutoff_day, d.sp_key, d.student_key, d.mp_key,
           d.num_of_prev_attempts, d.studied_credits, d.highest_education, d.date_registration,
           d.gender, d.disability, d.imd_band, d.age_band, d.region,
           d.final_result, d.at_risk
    from gold.dim_student_presentation d
    cross join run.cutoffs c
    where d.date_registration is not null
      and d.date_registration < c.cutoff_day
      and (d.date_unregistration is null or d.date_unregistration >= c.cutoff_day)
),
eng as (
    select p.cutoff_day, p.sp_key,
           sum(f.clicks) as clicks_total,
           sum(case when f.week_index >= p.cutoff_day / 7 - 2 then f.clicks else 0 end) as clicks_last_2w,
           sum(case when f.week_index >= p.cutoff_day / 7 - 4 and f.week_index < p.cutoff_day / 7 - 2
                    then f.clicks else 0 end) as clicks_prev_2w,
           sum(case when f.week_index < 0 then f.clicks else 0 end) as clicks_before_start,
           sum(f.active_days) as active_days_total,
           sum(case when f.week_index >= p.cutoff_day / 7 - 2 then f.active_days else 0 end) as active_days_last_2w,
           count(*) as weeks_active,
           max(f.last_active_day) as last_active_day
    from pop p
    join gold.fact_student_week f on f.sp_key = p.sp_key and f.week_index < p.cutoff_day / 7
    group by p.cutoff_day, p.sp_key
),
grp as (
    select p.cutoff_day, p.sp_key,
           sum(case when t.activity_group = 'Content' then f.clicks else 0 end) as clicks_content,
           sum(case when t.activity_group = 'Communication' then f.clicks else 0 end) as clicks_communication,
           sum(case when t.activity_group = 'Assessment practice' then f.clicks else 0 end) as clicks_quiz,
           sum(case when t.activity_group = 'Homepage' then f.clicks else 0 end) as clicks_homepage
    from pop p
    join gold.fact_engagement_weekly f on f.sp_key = p.sp_key and f.week_index < p.cutoff_day / 7
    join gold.dim_activity_type t on t.activity_type = f.activity_type
    group by p.cutoff_day, p.sp_key
),
due as (
    select p.cutoff_day, p.sp_key, count(*) as n_due
    from pop p
    join gold.dim_assessment a on a.mp_key = p.mp_key
    where a.assessment_type <> 'Exam' and a.due_day_effective < p.cutoff_day
    group by p.cutoff_day, p.sp_key
),
sub as (
    select p.cutoff_day, p.sp_key,
           sum(case when f.is_banked = 0 then 1 else 0 end) as n_submitted,
           sum(case when f.is_banked = 1 then 1 else 0 end) as n_banked,
           sum(case when a.due_day_effective < p.cutoff_day then 1 else 0 end) as n_submitted_due,
           avg(f.score) as avg_score,
           sum(case when f.days_late > 0 then 1 else 0 end) as n_late,
           avg(f.days_late) as avg_days_late
    from pop p
    join gold.fact_assessment f on f.sp_key = p.sp_key and f.date_submitted < p.cutoff_day
    join gold.dim_assessment a on a.assessment_key = f.assessment_key
    where a.assessment_type <> 'Exam'
    group by p.cutoff_day, p.sp_key
),
joined as (
    select p.cutoff_day, p.sp_key, p.student_key, p.mp_key,
           m.code_module, m.code_presentation, m.presentation_year,
           coalesce(e.clicks_total, 0) as clicks_total,
           coalesce(e.clicks_last_2w, 0) as clicks_last_2w,
           coalesce(e.clicks_prev_2w, 0) as clicks_prev_2w,
           coalesce(e.clicks_before_start, 0) as clicks_before_start,
           coalesce(e.active_days_total, 0) as active_days_total,
           coalesce(e.active_days_last_2w, 0) as active_days_last_2w,
           coalesce(e.weeks_active, 0) as weeks_active,
           -- days since the last active day; a student with no activity at all is given the days since registration opened
           case when e.last_active_day is null then p.cutoff_day + 30
                else p.cutoff_day - 1 - e.last_active_day end as days_since_last_active,
           coalesce(g.clicks_content, 0) as clicks_content,
           coalesce(g.clicks_communication, 0) as clicks_communication,
           coalesce(g.clicks_quiz, 0) as clicks_quiz,
           coalesce(g.clicks_homepage, 0) as clicks_homepage,
           coalesce(d.n_due, 0) as n_due,
           coalesce(s.n_submitted, 0) as n_submitted,
           coalesce(s.n_banked, 0) as n_banked,
           coalesce(d.n_due, 0) - coalesce(s.n_submitted_due, 0) as n_missed,
           s.avg_score,
           coalesce(s.n_late, 0) as n_late,
           s.avg_days_late,
           p.num_of_prev_attempts, p.studied_credits, p.highest_education,
           -p.date_registration as registered_days_before_start,
           p.gender, p.disability, p.imd_band, p.age_band, p.region,
           p.final_result, p.at_risk
    from pop p
    join gold.dim_module_presentation m on m.mp_key = p.mp_key
    left join eng e on e.cutoff_day = p.cutoff_day and e.sp_key = p.sp_key
    left join grp g on g.cutoff_day = p.cutoff_day and g.sp_key = p.sp_key
    left join due d on d.cutoff_day = p.cutoff_day and d.sp_key = p.sp_key
    left join sub s on s.cutoff_day = p.cutoff_day and s.sp_key = p.sp_key
)
select j.*,
       -- engagement relative to the same cohort at the same cut-off (uses only as-of data)
       j.clicks_total / nullif(median(j.clicks_total) over (partition by j.cutoff_day, j.mp_key), 0) as clicks_vs_cohort_median
from joined j
