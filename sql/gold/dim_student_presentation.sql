-- One row per student per module-presentation. The source student number is replaced by salted hashes:
-- sp_key identifies the row, student_key identifies the person across presentations (needed to keep one
-- person on one side of a train/test split). Neither can be reversed without the salt.
select substr(sha256(concat((select pseudonym_salt from run.settings), '|', r.code_module, '|', r.code_presentation, '|', cast(r.id_student as varchar))), 1, 12) as sp_key,
       substr(sha256(concat((select pseudonym_salt from run.settings), '|student|', cast(r.id_student as varchar))), 1, 12) as student_key,
       concat(r.code_module, '-', r.code_presentation) as mp_key,
       i.gender, i.region, i.highest_education, i.imd_band, i.age_band, i.disability,
       i.num_of_prev_attempts, i.studied_credits,
       r.date_registration,
       r.date_unregistration,
       i.final_result,
       case when i.final_result in ('Withdrawn', 'Fail') then 1
            when i.final_result in ('Pass', 'Distinction') then 0 end as at_risk
from silver.student_registration r
left join silver.student_info i
    on i.code_module = r.code_module and i.code_presentation = r.code_presentation and i.id_student = r.id_student
