"""Data contracts (each rule must fail on a planted defect) and the role-based access views."""
import random

import pytest

from oulad import access, contracts as dq
from oulad.lake import MemLake
from tests.helpers import build, mem_lake, rows, sp_key


@pytest.fixture()
def con():
    c = build(mem_lake(bad=False))
    c.execute("create table ops.batch_ledger as select 'studentVle' as source, _batch_week as batch_week, "
              "count(*) as \"rows\" from bronze.student_vle group by 2")
    return c


def failed(con, week=0):
    out = dq.run_all(con, dq.load_contracts(), [], week)
    return {r["check_id"]: r for r in out if not r["passed"]}


def test_clean_miniature_passes_every_error_check(con):
    out = dq.run_all(con, dq.load_contracts(), [], 0)
    assert len(out) > 150
    bad = [r for r in out if not r["passed"]]
    assert {r["check_id"] for r in bad} == {"silver.student_registration:registration_date_recorded",
                                            "silver.student_assessment:score_recorded",
                                            "silver.vle_daily:clicks_after_unregistration"}
    assert all(r["severity"] == "warn" for r in bad)
    assert {r["kind"] for r in out} == {"schema", "not_null", "range", "allowed", "regex", "unique", "foreign_key",
                                        "custom", "reconciliation"}


DEFECTS = {
    "silver.vle_daily:unique": "insert into silver.vle_daily select * from silver.vle_daily limit 1",
    "silver.student_info:gender:not_null": "update silver.student_info set gender = null where id_student = 3",
    "silver.student_info:gender:allowed": "update silver.student_info set gender = 'X' where id_student = 3",
    "silver.vle_daily:date:range": "update silver.vle_daily set date = 401 where id_student = 3",
    "silver.vle_daily:sum_click:range": "update silver.vle_daily set sum_click = 0 where id_student = 3",
    "silver.student_assessment:score:range": "update silver.student_assessment set score = 100.5 where id_student = 3",
    "silver.courses:code_presentation:regex": "update silver.courses set code_presentation = '2013X' where code_presentation = '2014J'",
    "silver.student_info:imd_band:regex": "update silver.student_info set imd_band = '10-20' where id_student = 1",
    "silver.vle_daily:fk:silver.vle_sites": "update silver.vle_daily set id_site = 555 where id_student = 3",
    "silver.student_assessment:fk:silver.assessments": "update silver.student_assessment set id_assessment = 77 where id_student = 3",
    "silver.assessments:only_exams_lack_a_due_day": "update silver.assessments set due_day = null where id_assessment = 11",
    "silver.vle_daily:event_week_matches_date": "update silver.vle_daily set event_week = 5 where id_student = 3",
    "gold.fact_student_week:unique": "insert into gold.fact_student_week select * from gold.fact_student_week limit 1",
    "gold.fact_student_week:active_days:range": "update gold.fact_student_week set active_days = 8",
    "gold.dim_student_presentation:sp_key:regex": "update gold.dim_student_presentation set sp_key = 'student-0001' where final_result = 'Distinction'",
    "gold.dim_student_presentation:at_risk:allowed": "update gold.dim_student_presentation set at_risk = 2 where final_result = 'Fail'",
    "gold.dim_student_presentation:no_source_student_number_in_gold": "alter table gold.fact_assessment add column id_student integer",
    "gold.fact_assessment:banked_rows_have_no_lateness": "update gold.fact_assessment set days_late = 0 where is_banked = 1",
    "gold.fact_assessment:fk:gold.dim_assessment": "delete from gold.dim_assessment where assessment_key = 12",
    "gold.dim_week:week_index:range": "update gold.dim_week set week_index = 40 where week_index = 39",
    "silver.assessments:due_day:type": "alter table silver.assessments alter due_day type double",
    "gold.dim_module_presentation:length_days:type": "alter table gold.dim_module_presentation drop column length_days",
    "silver.vle_sites:exists": "drop table silver.vle_sites",
    "reconcile:bronze_clicks_rows_accounted_for": "delete from silver.vle_daily where id_student = 3 and date = 5",
    "reconcile:silver_to_gold_clicks": "update gold.fact_student_week set clicks = clicks + 1 where week_index = 0",
    "reconcile:gold_facts_agree_on_clicks": "delete from gold.fact_engagement_weekly where week_index = 3",
    "reconcile:bronze_registrations_accounted_for": "update silver.student_registration set n_versions = 2 where id_student = 3",
    "reconcile:registrations_silver_to_gold": "delete from gold.dim_student_presentation where final_result = 'Distinction'",
    "reconcile:submissions_silver_to_gold": "delete from gold.fact_assessment where score = 30",
    "reconcile:ledger_matches_bronze_clicks": "update ops.batch_ledger set \"rows\" = \"rows\" + 1 where batch_week = 0",
}


@pytest.mark.parametrize("check_id", sorted(DEFECTS))
def test_each_rule_fails_on_its_planted_defect(con, check_id):
    assert check_id not in failed(con)
    con.execute(DEFECTS[check_id])
    assert check_id in failed(con)


def test_volume_ratio_and_window():
    assert dq.volume_ratio(50, [100, 100, 100, 100]) == 0.5
    assert dq.volume_ratio(300, [100, 200, 100, 400]) == 2.0        # median of the window is 150
    assert dq.volume_ratio(5, []) is None and dq.volume_ratio(5, [0, 0]) is None
    rule = dq.load_contracts()["volume"]
    ledger = [{"source": "studentVle", "batch_week": w, "rows": 100} for w in range(0, 10)]
    ledger += [{"source": "studentInfo", "batch_week": 10, "rows": 100000}]
    ok = dq.volume_check(ledger + [{"source": "studentVle", "batch_week": 10, "rows": 120}], 10, rule)
    assert ok[0]["passed"] and ok[0]["observed"] == "1.200" and ok[0]["severity"] == "warn"
    assert not dq.volume_check(ledger + [{"source": "studentVle", "batch_week": 10, "rows": 39}], 10, rule)[0]["passed"]
    assert not dq.volume_check(ledger + [{"source": "studentVle", "batch_week": 10, "rows": 251}], 10, rule)[0]["passed"]
    assert dq.volume_check(ledger + [{"source": "studentVle", "batch_week": 10, "rows": 40}], 10, rule)[0]["passed"]
    assert dq.volume_check(ledger + [{"source": "studentVle", "batch_week": 10, "rows": 250}], 10, rule)[0]["passed"]
    assert not dq.volume_check(ledger, 10, rule)[0]["passed"]          # nothing delivered at all
    assert dq.volume_check(ledger, 1, rule) == [] and dq.volume_check(ledger, 34, rule) == []
    # the window is the four weeks before, not all history
    old = [{"source": "studentVle", "batch_week": w, "rows": 1 if w < 6 else 100} for w in range(0, 10)]
    assert dq.volume_check(old + [{"source": "studentVle", "batch_week": 10, "rows": 100}], 10, rule)[0]["observed"] == "1.000"


# --- access ----------------------------------------------------------------------------------------------

PROTECTED = {"gender", "disability", "imd_band", "age_band", "region", "student_key"}


def session(con, user, role):
    access.install(con, user, role)
    return con


def test_lecturer_sees_only_own_module_presentations(con):
    session(con, "lecturer_aaa", "lecturer")
    assert {r[0] for r in rows(con, "select distinct mp_key from access.lecturer_students")} == {"AAA-2013J", "AAA-2014J"}
    con.execute("update gold.sec_lecturer_assignment set mp_key = 'AAA-2014J' where app_user = 'lecturer_aaa'")
    assert rows(con, "select distinct mp_key from access.lecturer_students") == [("AAA-2014J",)]
    assert {r[0] for r in rows(con, "select distinct mp_key from access.lecturer_student_week")} == {"AAA-2014J"}
    assert {r[0] for r in rows(con, "select distinct mp_key from access.lecturer_assessment")} == {"AAA-2014J"}
    assert rows(con, "select count(*) from access.lecturer_students")[0][0] == 2
    session(con, "lecturer_bbb", "lecturer")                 # teaches nothing here
    assert rows(con, "select count(*) from access.lecturer_students")[0][0] == 0
    assert rows(con, "select count(*) from access.lecturer_student_week")[0][0] == 0


def test_lecturer_views_carry_no_protected_attributes(con):
    session(con, "lecturer_aaa", "lecturer")
    for view in ("lecturer_students", "lecturer_student_week", "lecturer_assessment"):
        cols = {r[0] for r in con.execute(f"describe access.{view}").fetchall()}
        assert not cols & PROTECTED, view


def test_roles_are_separated(con):
    session(con, "lecturer_aaa", "lecturer")
    for view in ("mgmt_outcomes", "mgmt_outcomes_by_group", "mgmt_weekly_engagement", "engineer_students"):
        assert rows(con, f"select count(*) from access.{view}")[0][0] == 0, view
    session(con, "principal", "management")
    assert rows(con, "select count(*) from access.lecturer_students")[0][0] == 0
    assert rows(con, "select count(*) from access.lecturer_student_week")[0][0] == 0
    assert rows(con, "select count(*) from access.engineer_students")[0][0] == 0
    assert rows(con, "select count(*) from access.mgmt_outcomes")[0][0] == 2
    session(con, "someone", "data_engineer")
    assert rows(con, "select count(*) from access.engineer_students")[0][0] == 8
    assert rows(con, "select count(*) from access.lecturer_students")[0][0] == 8
    assert rows(con, "select count(*) from access.mgmt_outcomes")[0][0] == 2
    with pytest.raises(ValueError):
        access.install(con, "x", "superuser")


def _dim(lake, cells):
    """A gold.dim_student_presentation with the given (mp, gender, age_band, at_risk, n) cells."""
    lake.con.execute("create or replace table gold.dim_student_presentation (sp_key varchar, student_key varchar, "
                     "mp_key varchar, gender varchar, region varchar, highest_education varchar, imd_band varchar, "
                     "age_band varchar, disability varchar, num_of_prev_attempts integer, studied_credits integer, "
                     "date_registration integer, date_unregistration integer, final_result varchar, at_risk integer)")
    i = 0
    for mp, gender, age, risk, n in cells:
        for _ in range(n):
            i += 1
            lake.con.execute("insert into gold.dim_student_presentation values (?, ?, ?, ?, 'r', 'e', null, ?, 'N', 0, 60, "
                             "-5, null, ?, ?)", [f"k{i}", f"s{i}", mp, gender, age, "Fail" if risk else "Pass", risk])
    lake.con.execute("create or replace table gold.fact_student_week (sp_key varchar, mp_key varchar, clicks bigint, "
                     "active_days bigint, sites_visited bigint, first_active_day integer, last_active_day integer, week_index integer)")
    lake.con.execute("create or replace table gold.dim_module_presentation as select distinct mp_key from gold.dim_student_presentation")
    for t in ("gold.sec_lecturer_assignment (app_user varchar, mp_key varchar)",
              "gold.fact_assessment (sp_key varchar, mp_key varchar, assessment_key integer, assessment_type varchar, "
              "date_submitted integer, is_banked integer, score double, days_late integer)"):
        lake.con.execute(f"create or replace table {t}")
    return session(lake.con, "principal", "management")


def test_small_cells_are_withheld_with_complementary_suppression():
    con = _dim(MemLake(), [("M1", "M", "0-35", 0, 12), ("M1", "M", "0-35", 1, 8), ("M1", "F", "0-35", 1, 3),
                           ("M1", "F", "35-55", 0, 7), ("M1", "F", "55<=", 0, 6)])
    got = {(d, b): (n, k, why) for _, d, b, n, k, _, why in rows(con, "select * from access.mgmt_outcomes_by_group")}
    # age bands: 23, 7, 6 students -> nothing withheld, but at-risk counts are small in two of them
    assert got[("age_band", "0-35")] == (23, 11, None)
    assert got[("age_band", "35-55")] == (7, None, None) and got[("age_band", "55<=")] == (6, None, None)
    # gender: 20 M, 16 F -> shown; F has 3 at risk -> rate withheld
    assert got[("gender", "M")] == (20, 8, None) and got[("gender", "F")] == (16, None, None)

def test_gender_breakdown_with_one_small_cell_hides_two_cells():
    con = _dim(MemLake(), [("M1", "M", "0-35", 0, 20), ("M1", "F", "0-35", 0, 3)])
    got = {(d, b): (n, why) for _, d, b, n, _, _, why in rows(con, "select * from access.mgmt_outcomes_by_group")}
    assert got[("gender", "F")] == (None, "primary") and got[("gender", "M")] == (None, "complementary")
    assert got[("age_band", "0-35")] == (23, None)
    total = rows(con, "select n_students from access.mgmt_outcomes")[0][0]
    assert total == 23       # the total is published, so one hidden cell alone would be recoverable


def test_sql_suppression_matches_the_python_reference_and_resists_differencing():
    rng = random.Random(7)
    bands = ["0-35", "35-55", "55<="]
    for trial in range(40):
        counts = {b: rng.choice([0, 1, 2, 3, 4, 5, 6, 9, 30]) for b in bands}
        if sum(counts.values()) == 0:
            continue
        con = _dim(MemLake(), [("M1", "M", b, 0, n) for b, n in counts.items() if n])
        got = {b: n for _, d, b, n, _, _, _ in rows(con, "select * from access.mgmt_outcomes_by_group") if d == "age_band"}
        present = {b: n for b, n in counts.items() if n}
        assert got == access.suppress_breakdown(present), (trial, counts)
        assert not access.recoverable_by_differencing(got, sum(present.values())) or len(present) == 1, (trial, counts)
        for n in got.values():
            assert n is None or n >= 5


def test_python_suppression_reference():
    assert access.suppress_breakdown({"a": 10, "b": 7, "c": 5}) == {"a": 10, "b": 7, "c": 5}
    assert access.suppress_breakdown({"a": 10, "b": 7, "c": 4}) == {"a": 10, "b": None, "c": None}
    assert access.suppress_breakdown({"a": 10, "b": 2, "c": 4}) == {"a": 10, "b": None, "c": None}
    assert access.suppress_breakdown({"a": 7, "b": 7, "c": 1}) == {"a": None, "b": 7, "c": None}     # tie: label order
    assert access.suppress_breakdown({"a": 3}) == {"a": None}
    assert access.suppress_breakdown({"a": 6, "b": 4}, threshold=4) == {"a": 6, "b": 4}
    assert access.recoverable_by_differencing({"a": 10, "b": None}, 12)
    assert not access.recoverable_by_differencing({"a": None, "b": None}, 12)
    assert not access.recoverable_by_differencing({"a": 10, "b": 5}, 15)
    assert [access.suppress_rate(20, k) for k in (4, 5, 15, 16)] == [None, 5, 15, None]
    assert access.suppress_rate(4, 2) is None


def test_management_totals_and_weekly_engagement_are_suppressed(con):
    session(con, "principal", "management")
    # the miniature has 6 and 2 students: the second presentation is withheld entirely
    got = {r[0]: r[1:] for r in rows(con, "select * from access.mgmt_outcomes")}
    assert got["AAA-2013J"] == (6, None, None, None, False) and got["AAA-2014J"] == (None, None, None, None, True)
    weekly = rows(con, "select n_active_students, clicks, suppressed from access.mgmt_weekly_engagement")
    assert weekly and all(r == (None, None, True) for r in weekly)       # never more than 3 active students
    cols = {r[0] for r in con.execute("describe access.mgmt_outcomes_by_group").fetchall()}
    assert "sp_key" not in cols and "student_key" not in cols


def test_mgmt_outcome_counts_shown_when_large_enough():
    con = _dim(MemLake(), [("M1", "M", "0-35", 0, 12), ("M1", "M", "0-35", 1, 8)])
    assert rows(con, "select n_students, n_fail, n_pass, n_withdrawn from access.mgmt_outcomes") == [(20, 8, 12, None)]
