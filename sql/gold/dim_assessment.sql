select id_assessment as assessment_key,
       concat(code_module, '-', code_presentation) as mp_key,
       assessment_type,
       due_day,
       due_day_effective,
       due_day_missing,
       cast(floor(due_day_effective / 7.0) as integer) as due_week,
       weight
from silver.assessments
