-- SYNTHETIC teaching assignments: OULAD does not say who taught what. One made-up lecturer per module,
-- plus one who teaches two modules, so that the row-level rules have something to filter on.
select concat('lecturer_', lower(code_module)) as app_user, mp_key
from gold.dim_module_presentation
union all
select 'lecturer_two_modules' as app_user, mp_key
from gold.dim_module_presentation
where code_module in ('AAA', 'GGG')
