-- Role model. DuckDB has no users or grants, so these views show the *logic* of the three roles and are what
-- the tests exercise; on a real platform the same rules would be enforced by the warehouse and by the
-- row-level-security roles in the Power BI model. The session is one row in run.session(app_user, app_role).
-- gold.sec_lecturer_assignment is SYNTHETIC: OULAD does not say who taught what.
create or replace view access.my_module_presentations as
select m.mp_key
from gold.dim_module_presentation m
cross join run.session s
where s.app_role = 'data_engineer'
   or (s.app_role = 'lecturer'
       and m.mp_key in (select a.mp_key from gold.sec_lecturer_assignment a where a.app_user = s.app_user))
