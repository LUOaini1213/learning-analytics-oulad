-- Lecturer: student-level rows, only for the lecturer's own module-presentations, and without the
-- protected attributes (gender, disability, deprivation band, age band, region), which a lecturer does not
-- need in order to follow up a student.
create or replace view access.lecturer_students as
select d.sp_key, d.mp_key, d.num_of_prev_attempts, d.studied_credits, d.date_registration,
       d.date_unregistration, d.final_result
from gold.dim_student_presentation d
where d.mp_key in (select mp_key from access.my_module_presentations);

create or replace view access.lecturer_student_week as
select f.sp_key, f.mp_key, f.week_index, f.clicks, f.active_days, f.sites_visited, f.last_active_day
from gold.fact_student_week f
where f.mp_key in (select mp_key from access.my_module_presentations);

create or replace view access.lecturer_assessment as
select f.sp_key, f.mp_key, f.assessment_key, f.assessment_type, f.date_submitted, f.is_banked, f.score, f.days_late
from gold.fact_assessment f
where f.mp_key in (select mp_key from access.my_module_presentations)
