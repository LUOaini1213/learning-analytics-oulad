-- Every rejected source row, with the reason and the raw values, kept for investigation and replay.
select 'courses' as source_table, _batch_week as batch_week, _source_file as source_file, _source_row as source_row,
       _reject_reason as reject_reason, _raw as raw_record, cast(null as integer) as event_week
from stage.courses where _reject_reason is not null
union all
select 'assessments', _batch_week, _source_file, _source_row, _reject_reason, _raw, null
from stage.assessments where _reject_reason is not null
union all
select 'vle', _batch_week, _source_file, _source_row, _reject_reason, _raw, null
from stage.vle_sites where _reject_reason is not null
union all
select 'student_registration', _batch_week, _source_file, _source_row, _reject_reason, _raw, null
from stage.student_registration where _reject_reason is not null
union all
select 'student_info', _batch_week, _source_file, _source_row, _reject_reason, _raw, null
from stage.student_info where _reject_reason is not null
union all
select 'student_assessment', _batch_week, _source_file, _source_row, _reject_reason, _raw, null
from stage.student_assessment where _reject_reason is not null
union all
select 'student_vle', _batch_week, _source_file, _source_row, _reject_reason, _raw, event_week
from stage.student_vle where _reject_reason is not null
