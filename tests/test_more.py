"""Cases added after the first mutation run showed which planted bugs no test noticed."""
import numpy as np
import pytest

from oulad import access
from tests.helpers import build, mem_lake, rows, sp_key


def feat(con, cutoff, student):
    k = sp_key(con, "2013J", student)
    cur = con.execute(f"select * from gold.features_early_warning where cutoff_day = {cutoff} and sp_key = '{k}'")
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, cur.fetchall()[0]))


def test_active_days_counts_days_not_material_days():
    # student 3 opens a second material on day 5: still two active days (5 and 6) in week 0
    con = build(mem_lake(bad=False, extra={"studentVle": [("AAA", "2013J", "3", "100", "5", "9")]}))
    k = sp_key(con, "2013J", 3)
    assert rows(con, f"select active_days, sites_visited, clicks from gold.fact_student_week where sp_key = '{k}'") == [(2, 2, 13)]
    assert rows(con, f"select activity_type, active_days from gold.fact_engagement_weekly where sp_key = '{k}' order by 1") == \
        [("forumng", 2), ("resource", 1)]


def test_submission_on_the_due_day_is_not_late():
    # student 3 hands in assessment 12 on day 20, its due day
    con = build(mem_lake(bad=False, extra={"studentAssessment": [("12", "3", "20", "0", "50")]}), [28])
    f = feat(con, 28, 3)
    assert (f["n_submitted"], f["n_late"], f["avg_days_late"]) == (2, 1, 1.0)
    assert rows(con, "select days_late from gold.fact_assessment where assessment_key = 12 and score = 50") == [(0,)]


def test_volume_check_flags_a_week_it_cannot_compare():
    from oulad import contracts as dq
    rule = dq.load_contracts()["volume"]
    ledger = [{"source": "studentVle", "batch_week": w, "rows": 0} for w in range(6, 10)]
    out = dq.volume_check(ledger + [{"source": "studentVle", "batch_week": 10, "rows": 100}], 10, rule)
    assert out[0]["observed"] == "n/a" and not out[0]["passed"]       # nothing to compare with is not a pass


def test_paired_bootstrap_resamples_both_scores_together():
    from oulad.analytics import metrics as M
    rng = np.random.default_rng(11)
    y = rng.integers(0, 2, 500)
    b = 0.3 * y + rng.normal(0, 1, 500)
    a = b + 0.2 * y                      # a small, consistent improvement on the same students
    r = M.paired_bootstrap(M.average_precision, y, a, b, 200, 3)
    assert r["difference"] > 0 and r["ci_low"] > 0 and r["ci_high"] - r["ci_low"] < 0.08
    same = M.paired_bootstrap(M.average_precision, y, b, b, 50, 3)
    assert same["ci_low"] == same["ci_high"] == 0.0 and same["share_of_resamples_above_zero"] == 0.0


def test_first_reason_is_the_largest_positive_contribution():
    from oulad.analytics import model
    from tests.test_analytics import synthetic
    df = model.prepare(synthetic())
    train, test, _ = model.split(df)
    lin = model.Linear().fit(model.design(train), train["at_risk"])
    x = model.design(test).head(40)
    contrib = lin.contributions(x)
    reasons = lin.reasons(x)
    for i in range(len(x)):
        row = contrib.iloc[i]
        top = row.idxmax()
        assert row[top] > 0
        base = top[: -len("_missing")] if top.endswith("_missing") else top
        v = x.iloc[i][base]
        want = model.REASONS[base + "_missing"] if (top.endswith("_missing") or v != v) else model.REASONS[top].format(v=float(v))
        assert reasons[i][0] == want, (i, top)
        assert len(reasons[i]) == min(3, int((row > 0).sum()))


def test_withdrawal_extract_withholds_small_counts():
    """The dashboard's withdrawal table, built from a hand-made class of 20 of whom 6 leave on day 10."""
    from oulad.lake import MemLake
    from scripts.export_dashboard import WITHDRAWAL_SQL
    con = MemLake().con
    con.execute("create table gold.dim_week as select cast(w as integer) as week_index, 7 * w as day_from, 7 * w + 6 as day_to "
                "from range(-5, 40) t(w)")
    con.execute("create table gold.dim_student_presentation as "
                "select 'M1' as mp_key, case when i < 6 then 10 when i = 6 then 20 when i = 19 then -3 end as date_unregistration "
                "from range(21) t(i)")
    got = {w: (n, left) for _, w, n, left in con.execute(WITHDRAWAL_SQL).fetchall()}
    assert len(got) == 34 and got[0] == (None, None)        # nobody has left yet in week 0: withheld, not zero
    assert got[1] == (20, 6)                                # the student who left before day 0 is not followed
    assert got[2] == got[33] == (20, 7)
    # 20 followed, 17 leave by day 10: only 3 remain, so the cell is withheld
    con.execute("create or replace table gold.dim_student_presentation as "
                "select 'M1' as mp_key, case when i < 17 then 10 end as date_unregistration from range(20) t(i)")
    assert all(n is None and left is None for _, w, n, left in con.execute(WITHDRAWAL_SQL).fetchall() if w >= 1)
