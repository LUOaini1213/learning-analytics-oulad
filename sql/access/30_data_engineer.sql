-- Data engineer: everything in gold, plus the quarantine and the operational tables. Nothing is masked;
-- the role exists so that the difference from the other two is explicit and testable.
create or replace view access.engineer_students as
select d.*
from gold.dim_student_presentation d
cross join run.session s
where s.app_role = 'data_engineer'
