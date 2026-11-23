"""As-of features: hand-worked values, the known traps, and the leakage detector with its canaries."""
import math

import pytest

from oulad import leakage, transform
from tests.helpers import build, mem_lake, rows, source_rows, sp_key

CUTS = [7, 14, 28]


@pytest.fixture(scope="module")
def con():
    return build(mem_lake(bad=False), CUTS)


def feat(con, cutoff, presentation, student):
    k = sp_key(con, presentation, student)
    cur = con.execute(f"select * from gold.features_early_warning where cutoff_day = {cutoff} and sp_key = '{k}'")
    cols = [d[0] for d in cur.description]
    got = cur.fetchall()
    return dict(zip(cols, got[0])) if got else None


def test_population_at_each_cutoff(con):
    def who(c):
        return {s for s in range(1, 7) if feat(con, c, "2013J", s)}
    assert who(7) == {1, 2, 3, 6}       # 4: registration date unknown; 5: registers on day 20
    assert who(14) == {1, 3, 6}         # 2 unregistered on day 12, before the cut-off
    assert who(28) == {1, 3, 5, 6}      # 5 has registered; 6 leaves on day 30, after the cut-off
    assert rows(con, "select cutoff_day, count(*) from gold.features_early_warning group by 1 order by 1") == \
        [(7, 6), (14, 5), (28, 6)]


def test_student_who_leaves_on_the_cutoff_day_is_still_in_and_registering_on_it_is_out():
    src = source_rows(False)
    src["studentRegistration"] = [("AAA", "2013J", "2", "-5", "14") if r[2] == "2" else
                                  ("AAA", "2013J", "5", "14", "?") if r[2] == "5" else r
                                  for r in src["studentRegistration"]]
    c = build(mem_lake(rows=src), [14])
    assert feat(c, 14, "2013J", 2) is not None      # known only at the end of day 14; the snapshot is of day 13
    assert feat(c, 14, "2013J", 5) is None


def test_engagement_features_day_14(con):
    f = feat(con, 14, "2013J", 1)
    assert (f["clicks_total"], f["clicks_last_2w"], f["clicks_prev_2w"], f["clicks_before_start"]) == (16, 11, 5, 5)
    assert (f["active_days_total"], f["active_days_last_2w"], f["weeks_active"]) == (4, 3, 3)
    assert f["days_since_last_active"] == 0          # active on day 13, the last day before the cut-off
    assert (f["clicks_content"], f["clicks_communication"], f["clicks_quiz"], f["clicks_homepage"]) == (5, 4, 1, 6)
    assert f["clicks_vs_cohort_median"] == 4.0       # cohort clicks 16, 4, 0 -> median 4
    assert (f["code_module"], f["code_presentation"], f["presentation_year"]) == ("AAA", "2013J", 2013)


def test_engagement_features_day_28(con):
    f = feat(con, 28, "2013J", 1)
    assert (f["clicks_total"], f["clicks_last_2w"], f["clicks_prev_2w"]) == (24, 8, 11)
    assert f["days_since_last_active"] == 0 and f["weeks_active"] == 5
    g = feat(con, 28, "2013J", 3)
    assert (g["clicks_total"], g["clicks_last_2w"], g["days_since_last_active"]) == (4, 0, 21)


def test_student_with_no_activity(con):
    f = feat(con, 14, "2013J", 6)
    assert (f["clicks_total"], f["weeks_active"], f["active_days_total"]) == (0, 0, 0)
    assert f["days_since_last_active"] == 44 and f["clicks_vs_cohort_median"] == 0.0
    assert f["registered_days_before_start"] == 20


def test_assessment_features(con):
    a = feat(con, 14, "2013J", 1)
    assert (a["n_due"], a["n_submitted"], a["n_missed"], a["n_late"], a["avg_score"], a["avg_days_late"]) == (1, 1, 0, 0, 80.0, -1.0)
    b = feat(con, 28, "2013J", 1)
    assert (b["n_due"], b["n_submitted"], b["n_missed"], b["n_late"], b["avg_score"], b["avg_days_late"]) == (2, 2, 0, 1, 70.0, 0.5)
    c = feat(con, 14, "2013J", 3)
    assert (c["n_due"], c["n_submitted"], c["n_late"], c["avg_score"], c["avg_days_late"]) == (1, 1, 1, 40.0, 2.0)
    d = feat(con, 28, "2013J", 5)
    assert (d["n_due"], d["n_submitted"], d["n_missed"]) == (2, 0, 2) and d["avg_score"] is None


def test_submission_on_or_after_the_cutoff_is_not_seen(con):
    # student 3 submits on day 12: invisible at day 7, and assessment 11 (due day 10) is not yet due
    f = feat(con, 7, "2013J", 3)
    assert (f["n_due"], f["n_submitted"], f["n_missed"]) == (0, 0, 0) and f["avg_score"] is None
    # student 1 submits assessment 12 on day 22: not seen at day 14
    assert feat(con, 14, "2013J", 1)["n_submitted"] == 1


def test_exam_with_no_date_is_never_counted_as_due(con):
    # assessment 13 is an exam with no date: it must not turn into "due on day 0 and missed"
    for c in CUTS:
        for s in (1, 3, 6):
            f = feat(con, c, "2013J", s)
            assert f["n_due"] <= 2 and f["n_missed"] <= 2
    assert feat(con, 7, "2013J", 1)["n_due"] == 0


def test_banked_result_counts_as_satisfied_but_not_as_a_submission(con):
    f = feat(con, 14, "2013J", 6)
    assert (f["n_due"], f["n_banked"], f["n_submitted"], f["n_missed"], f["n_late"]) == (1, 1, 0, 0, 0)
    assert f["avg_score"] == 70.0 and f["avg_days_late"] is None
    g = feat(con, 28, "2013J", 6)
    assert (g["n_due"], g["n_missed"]) == (2, 1)


def test_labels_are_carried_only_as_targets(con):
    assert feat(con, 14, "2013J", 1)["at_risk"] == 0 and feat(con, 14, "2013J", 3)["at_risk"] == 1
    assert feat(con, 14, "2013J", 6)["final_result"] == "Withdrawn"
    cols = [r[0] for r in con.execute("describe gold.features_early_warning").fetchall()]
    assert "date_unregistration" not in cols and "id_student" not in cols


def test_same_person_has_one_student_key_across_presentations(con):
    a, b = feat(con, 14, "2013J", 1), feat(con, 14, "2014J", 1)
    assert a["student_key"] == b["student_key"] and a["sp_key"] != b["sp_key"]
    assert feat(con, 14, "2014J", 7)["student_key"] != a["student_key"]


def test_cutoffs_must_be_whole_weeks(con):
    for bad in ([10], [0], [-7], [14, 15]):
        with pytest.raises(ValueError):
            transform.build_features(mem_lake(bad=False), con, transform.Steps("x"), bad, "gold.tmp")


# --- leakage ---------------------------------------------------------------------------------------------

def test_honest_features_do_not_move_when_the_future_is_rewritten(con):
    for c in CUTS:
        assert leakage.leaking_columns(con, transform.features_sql(), c) == []


def test_the_perturbation_really_changes_the_future(con):
    altered = leakage.copy_gold(con)
    before = altered.execute("select sum(clicks) from gold.fact_student_week where week_index >= 2").fetchone()[0]
    past = altered.execute("select sum(clicks) from gold.fact_student_week where week_index < 2").fetchone()[0]
    leakage.perturb_future(altered, 14)
    assert altered.execute("select sum(clicks) from gold.fact_student_week where week_index >= 2").fetchone()[0] != before
    assert altered.execute("select sum(clicks) from gold.fact_student_week where week_index < 2").fetchone()[0] == past
    assert altered.execute("select count(*) from gold.dim_student_presentation d join "
                           "(select sp_key, final_result from gold.dim_student_presentation) o using (sp_key)").fetchone()[0] == 8
    assert con.execute("select count(distinct final_result) from gold.dim_student_presentation").fetchone()[0] == 4
    assert altered.execute("select count(distinct final_result) from gold.dim_student_presentation").fetchone()[0] <= 2


LEAKS = {
    "all-course clicks": ("select f.*, t.c as leak from ({sql}) f left join (select sp_key, sum(clicks) as c "
                          "from gold.fact_student_week group by 1) t using (sp_key)", "leak"),
    "unregistration date": ("select f.*, d.date_unregistration as leak from ({sql}) f "
                            "join gold.dim_student_presentation d using (sp_key)", "leak"),
    "final result under another name": ("select f.*, d.final_result as leak from ({sql}) f "
                                        "join gold.dim_student_presentation d using (sp_key)", "leak"),
    "average score over all submissions": ("select f.*, t.s as leak from ({sql}) f left join (select sp_key, "
                                           "avg(score) as s from gold.fact_assessment group by 1) t using (sp_key)", "leak"),
}


@pytest.mark.parametrize("name", sorted(LEAKS))
def test_canary_an_injected_future_feature_is_caught(con, name):
    template, column = LEAKS[name]
    assert leakage.leaking_columns(con, template.format(sql=transform.features_sql()), 14) == [column]


def test_canary_dropping_the_week_filter_is_caught(con):
    sql = transform.features_sql()
    needle = "on f.sp_key = p.sp_key and f.week_index < p.cutoff_day / 7\n    group by p.cutoff_day, p.sp_key\n),\ngrp"
    assert needle in sql
    leaky = sql.replace(needle, "on f.sp_key = p.sp_key\n    group by p.cutoff_day, p.sp_key\n),\ngrp")
    found = leakage.leaking_columns(con, leaky, 14)
    assert "clicks_total" in found and "clicks_content" not in found


def test_canary_population_defined_with_hindsight_is_caught(con):
    sql = transform.features_sql()
    needle = "(d.date_unregistration is null or d.date_unregistration >= c.cutoff_day)"
    assert needle in sql
    leaky = sql.replace(needle, "d.date_unregistration is null")      # "students who never withdrew"
    assert leakage.leaking_columns(con, leaky, 14) == ["<population>"]


def test_no_nan_in_feature_table(con):
    for r in rows(con, "select clicks_vs_cohort_median from gold.features_early_warning"):
        assert r[0] is None or not math.isnan(r[0])
