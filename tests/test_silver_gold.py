"""Silver and gold transformations on the hand-written miniature (tests/helpers.py)."""
import pytest

from oulad import lineage, transform
from oulad.common import as_of_day, week_of
from tests.helpers import BAD_CLICKS, CLICKS, REGISTRATION, SOURCES, build, mem_lake, rows, sp_key


@pytest.fixture(scope="module")
def con():
    return build(mem_lake(bad=True))


def test_week_arithmetic():
    assert [week_of(d) for d in (-8, -7, -1, 0, 6, 7, 13, 14)] == [-2, -1, -1, 0, 0, 1, 1, 2]
    assert as_of_day(1) == 13 and as_of_day(-1) == -1
    assert week_of(as_of_day(3)) == 3 and week_of(as_of_day(3) + 1) == 4


def test_clicks_are_summed_per_student_material_day_not_deduplicated(con):
    got = rows(con, "select sum_click, n_source_rows, event_week from silver.vle_daily "
                    "where id_student = 1 and id_site = 100 and date = -3")
    assert got == [(5, 2, -1)]          # two source rows of 2 and 3 clicks on the same day
    assert rows(con, "select count(*), sum(sum_click) from silver.vle_daily") == [(12, 53)]


def test_each_bad_click_row_is_quarantined_with_its_reason(con):
    got = dict(rows(con, "select reject_reason, count(*) from silver.quarantine where source_table = 'student_vle' group by 1"))
    assert got == {"orphan_site": 1, "orphan_student": 1, "bad_date": 1, "click_count_out_of_range": 1,
                   "date_out_of_range": 1}
    raw = rows(con, "select raw_record from silver.quarantine where reject_reason = 'orphan_site'")
    assert raw == [("AAA|2013J|1|999|2|1",)]


def test_bad_submissions_are_quarantined(con):
    got = dict(rows(con, "select reject_reason, count(*) from silver.quarantine where source_table = 'student_assessment' group by 1"))
    assert got == {"orphan_assessment": 1, "orphan_student": 1, "score_out_of_range": 1}
    assert rows(con, "select count(*) from silver.student_assessment") == [(8,)]


def test_every_bronze_click_row_is_accounted_for(con):
    bronze = rows(con, "select count(*) from bronze.student_vle")[0][0]
    silver = rows(con, "select sum(n_source_rows) from silver.vle_daily")[0][0]
    rejected = rows(con, "select count(*) from silver.quarantine where source_table = 'student_vle'")[0][0]
    assert (bronze, silver, rejected) == (len(CLICKS) + len(BAD_CLICKS), len(CLICKS), len(BAD_CLICKS))


def test_exam_without_a_date_is_due_at_the_end_of_the_presentation(con):
    got = rows(con, "select id_assessment, due_day, due_day_effective, due_day_missing from silver.assessments order by 1")
    assert got == [(11, 10, 10, False), (12, 20, 20, False), (13, None, 100, True), (21, 10, 10, False)]


def test_question_mark_means_missing_and_imd_band_is_conformed(con):
    got = dict(rows(con, "select id_student, imd_band from silver.student_info where code_presentation = '2013J'"))
    assert got[1] == "10-20%" and got[3] is None and got[2] == "0-10%"
    reg = dict(rows(con, "select id_student, date_registration from silver.student_registration where code_presentation = '2013J'"))
    assert reg[4] is None and reg[1] == -10


def test_latest_delivery_of_a_registration_wins():
    # student 2 is first delivered without an unregistration date, then again with it
    first = [r if r[2] != "2" else ("AAA", "2013J", "2", "-5", "?") for r in REGISTRATION]
    lake = mem_lake(bad=False, rows={**__import__("tests.helpers", fromlist=["x"]).source_rows(False),
                                     "studentRegistration": first},
                    extra={"studentRegistration": [("AAA", "2013J", "2", "-5", "12", 1)]})
    con = build(lake)
    assert rows(con, "select date_unregistration, n_versions, first_batch_week, last_batch_week "
                     "from silver.student_registration where id_student = 2") == [(12, 2, -5, 1)]
    # every bronze version is accounted for
    assert rows(con, "select sum(n_versions) from silver.student_registration")[0][0] == \
        rows(con, "select count(*) from bronze.student_registration")[0][0]


def test_older_delivery_does_not_overwrite_a_newer_one():
    lake = mem_lake(bad=False, extra={"studentRegistration": [("AAA", "2013J", "1", "-10", "50", -6)]})
    con = build(lake)
    assert rows(con, "select date_unregistration from silver.student_registration "
                     "where id_student = 1 and code_presentation = '2013J'") == [(None,)]


def test_bad_reference_rows_are_rejected():
    lake = mem_lake(bad=False, extra={
        "courses": [("ZZZ", "2013X", "100"), ("ZZZ", "2013J", "0")],
        "assessments": [("AAA", "2013J", "31", "Quiz", "5", "10"), ("AAA", "2013J", "32", "TMA", "5", "150"),
                        ("QQQ", "2013J", "33", "TMA", "5", "10")],
        "studentInfo": [("AAA", "2013J", "1", "X", "r", "e", "?", "0-35", "0", "60", "N", "Pass"),
                        ("AAA", "2013J", "1", "M", "r", "e", "?", "0-35", "0", "60", "N", "Maybe")]})
    con = build(lake)
    got = set(rows(con, "select source_table, reject_reason from silver.quarantine"))
    assert got == {("courses", "bad_presentation_code"), ("courses", "length_out_of_range"),
                   ("assessments", "bad_assessment_type"), ("assessments", "weight_out_of_range"),
                   ("assessments", "orphan_module_presentation"), ("student_info", "bad_gender"),
                   ("student_info", "bad_final_result")}


def test_gold_has_no_source_student_number_and_keys_are_pseudonymous(con):
    cols = rows(con, "select table_name, column_name from information_schema.columns where table_schema = 'gold'")
    assert not [c for c in cols if c[1] == "id_student"]
    keys = rows(con, "select sp_key, student_key from gold.dim_student_presentation")
    assert len({k[0] for k in keys}) == len(REGISTRATION) == 8
    assert all(len(k[0]) == 12 and k[0] not in ("1", "2") for k in keys)
    # student 1 appears twice: two row keys, one person key
    person = rows(con, "select student_key, count(*) from gold.dim_student_presentation group by 1 having count(*) > 1")
    assert len(person) == 1 and person[0][1] == 2
    assert sp_key(con, "2013J", 1) in {k[0] for k in keys}


def test_changing_the_salt_changes_every_key():
    a = build(mem_lake(bad=False))
    lake = mem_lake(bad=False)
    lake.con.execute("set variable pseudonym_salt = 'another salt'")
    b = build(lake)
    ka = {r[0] for r in rows(a, "select sp_key from gold.dim_student_presentation")}
    kb = {r[0] for r in rows(b, "select sp_key from gold.dim_student_presentation")}
    assert len(ka) == len(kb) == 8 and not ka & kb


def test_at_risk_label(con):
    got = dict(rows(con, "select final_result, max(at_risk) from gold.dim_student_presentation group by 1"))
    assert got == {"Pass": 0, "Distinction": 0, "Fail": 1, "Withdrawn": 1}


def test_fact_student_week(con):
    k = sp_key(con, "2013J", 1)
    got = rows(con, f"select week_index, clicks, active_days, sites_visited, first_active_day, last_active_day "
                    f"from gold.fact_student_week where sp_key = '{k}' order by 1")
    assert got == [(-1, 5, 1, 1, -3, -3), (0, 4, 1, 1, 0, 0), (1, 7, 2, 2, 8, 13), (2, 7, 1, 1, 14, 14), (3, 1, 1, 1, 27, 27)]


def test_fact_engagement_weekly_and_activity_groups(con):
    k = sp_key(con, "2013J", 1)
    got = rows(con, f"select week_index, activity_type, clicks from gold.fact_engagement_weekly where sp_key = '{k}' order by 1, 2")
    assert got == [(-1, "resource", 5), (0, "forumng", 4), (1, "homepage", 6), (1, "quiz", 1), (2, "resource", 7), (3, "resource", 1)]
    groups = dict(rows(con, "select activity_type, activity_group from gold.dim_activity_type"))
    assert groups == {"resource": "Content", "forumng": "Communication", "quiz": "Assessment practice", "homepage": "Homepage"}
    assert rows(con, "select sum(clicks) from gold.fact_engagement_weekly") == rows(con, "select sum(clicks) from gold.fact_student_week")


def test_fact_assessment_lateness_and_banked_rows(con):
    got = rows(con, "select assessment_key, days_late, is_banked, score from gold.fact_assessment f "
                    "join gold.dim_student_presentation d using (sp_key) where d.mp_key = 'AAA-2013J' order by assessment_key, days_late")
    assert (11, -1, 0, 80.0) in got and (12, 2, 0, 60.0) in got and (11, 2, 0, 40.0) in got
    assert (11, None, 1, 70.0) in got          # banked: no lateness
    assert (13, -5, 0, 55.0) in got            # exam measured against day 100
    assert (11, 1, 0, None) in got             # submitted, not yet scored


def test_dim_module_presentation_and_week(con):
    assert rows(con, "select mp_key, presentation_year, start_month, length_days, last_week, n_registrations "
                     "from gold.dim_module_presentation order by 1") == \
        [("AAA-2013J", 2013, "October", 100, 14, 6), ("AAA-2014J", 2014, "October", 100, 14, 2)]
    assert rows(con, "select week_index, day_from, day_to, phase from gold.dim_week where week_index in (-1, 0, 2) order by 1") == \
        [(-1, -7, -1, "Before start"), (0, 0, 6, "Teaching"), (2, 14, 20, "Teaching")]
    assert rows(con, "select min(week_index), max(week_index), count(*) from gold.dim_week") == [(-5, 39, 45)]


def test_only_affected_weeks_are_recomputed():
    lake = mem_lake(bad=False)
    con = build(lake)
    # a late row for week 0 arrives in batch 2; only week 0 is rebuilt, and the row is added to the day's total
    con.execute("insert into bronze.student_vle values ('AAA', '2013J', '1', '101', '0', '10', 2, 'f', 'x', 99, 'r', 't')")
    con.execute("update silver.vle_daily set sum_click = 999 where event_week = 1")     # must survive untouched
    steps = transform.Steps("late")
    weeks = transform.run_silver(lake, con, steps, [2])
    assert weeks == [0, 2]      # week 0 from the late row, week 2 because batch 2 also holds its own rows
    assert rows(con, "select sum_click, n_source_rows, first_batch_week, last_batch_week from silver.vle_daily "
                     "where id_student = 1 and date = 0") == [(14, 2, 0, 2)]
    assert rows(con, "select count(*) from silver.vle_daily where sum_click = 999 and event_week = 1")[0][0] == 3
    transform.run_gold(lake, con, steps, weeks)
    k = sp_key(con, "2013J", 1)
    assert rows(con, f"select clicks from gold.fact_student_week where sp_key = '{k}' and week_index = 0") == [(14,)]


def test_orphan_clicks_are_released_when_the_student_arrives():
    src = __import__("tests.helpers", fromlist=["x"]).source_rows(False)
    src["studentRegistration"] = [r for r in src["studentRegistration"] if r[2] != "3"]
    lake = mem_lake(rows=src)
    con = build(lake)
    assert rows(con, "select reject_reason, count(*) from silver.quarantine group by 1 order by 1") == \
        [("orphan_student", 3)]     # two clicks and one submission
    con.execute("insert into bronze.student_registration values ('AAA', '2013J', '3', '3', '?', 4, 'f', 'x', 99, 'r', 't')")
    weeks = transform.run_silver(lake, con, transform.Steps("fix"), [4])
    assert weeks == [0] and rows(con, "select count(*) from silver.quarantine") == [(0,)]
    assert rows(con, "select sum(sum_click) from silver.vle_daily where id_student = 3") == [(4,)]


def test_lineage_inputs_come_from_the_sql(con):
    stage = transform.stage_sql()
    assert lineage.referenced_tables(transform.read_sql("silver", "vle_daily.sql"), stage) == \
        ["bronze.student_vle", "silver.vle_sites", "silver.student_registration"]
    assert lineage.referenced_tables("select 1 from gold.a join gold.a -- silver.zzz\n join silver.b") == ["gold.a", "silver.b"]
    assert set(lineage.referenced_tables(transform.read_sql("gold", "fact_assessment.sql"))) == \
        {"silver.student_assessment", "silver.assessments"}
    assert set(lineage.referenced_tables(transform.features_sql())) == {
        "gold.dim_student_presentation", "gold.fact_student_week", "gold.fact_engagement_weekly",
        "gold.dim_activity_type", "gold.dim_assessment", "gold.fact_assessment", "gold.dim_module_presentation"}


def test_every_sql_file_is_a_single_select():
    for folder in ("silver", "gold", "features"):
        for p in sorted((transform.SQL / folder).glob("*.sql")):
            body = lineage.strip_comments(p.read_text(encoding="utf-8")).strip().lower()
            assert body.startswith(("select", "with")), p.name
            assert ";" not in body, p.name
    assert len(SOURCES) == 7
