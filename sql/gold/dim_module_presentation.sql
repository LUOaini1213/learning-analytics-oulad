select concat(c.code_module, '-', c.code_presentation) as mp_key,
       c.code_module,
       c.code_presentation,
       cast(substr(c.code_presentation, 1, 4) as integer) as presentation_year,
       case substr(c.code_presentation, 5, 1) when 'B' then 'February' else 'October' end as start_month,
       c.module_presentation_length as length_days,
       cast(floor(c.module_presentation_length / 7.0) as integer) as last_week,
       coalesce(r.n_registrations, 0) as n_registrations
from silver.courses c
left join (
    select code_module, code_presentation, count(*) as n_registrations
    from silver.student_registration
    group by code_module, code_presentation
) r on r.code_module = c.code_module and r.code_presentation = c.code_presentation
