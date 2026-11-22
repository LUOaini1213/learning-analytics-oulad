-- Weeks are counted on each presentation's own clock: week 0 is days 0 to 6 after the start.
select week as week_index,
       7 * week as day_from,
       7 * week + 6 as day_to,
       case when week < 0 then concat('W', cast(week as varchar)) else concat('W', lpad(cast(week as varchar), 2, '0')) end as week_label,
       case when week < 0 then 'Before start' else 'Teaching' end as phase
from run.weeks
