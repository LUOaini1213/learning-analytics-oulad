"""Metrics against hand-worked cases, and the modelling code on synthetic data with known answers."""
import math

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score

from oulad.analytics import clustering, model
from oulad.analytics import metrics as M


# --- metrics -------------------------------------------------------------------------------------------------

def test_average_precision_by_hand():
    # ranking: 1, 0, 1, 0 -> precision at the positives 1/1 and 2/3 -> mean 5/6
    assert M.average_precision([1, 0, 1, 0], [0.9, 0.8, 0.7, 0.1]) == pytest.approx(5 / 6)
    assert M.average_precision([1, 1, 0, 0], [4, 3, 2, 1]) == 1.0
    assert M.average_precision([0, 0, 1, 1], [4, 3, 2, 1]) == pytest.approx((1 / 3 + 2 / 4) / 2)
    assert math.isnan(M.average_precision([0, 0], [1, 2]))


def test_average_precision_with_ties_and_against_scikit_learn():
    assert M.average_precision([1, 0, 1, 0], [1, 1, 1, 1]) == pytest.approx(0.5)       # all tied -> prevalence
    assert M.average_precision([0, 1], [0.5, 0.5]) == pytest.approx(0.5)               # order within a tie is irrelevant
    rng = np.random.default_rng(1)
    for _ in range(20):
        y = rng.integers(0, 2, 200)
        s = np.round(rng.random(200) + 0.3 * y, 1)                                     # many ties
        assert M.average_precision(y, s) == pytest.approx(average_precision_score(y, s))


def test_top_k_rounds_up():
    assert [M.top_k(n, 0.2) for n in (1, 4, 5, 6, 10, 11)] == [1, 1, 1, 2, 2, 3]
    assert M.top_k(10, 0.1) == 1 and M.top_k(30, 0.1) == 3 and M.top_k(3, 0.0) == 1


def test_flag_top_and_precision_recall():
    y = np.array([1, 1, 0, 1, 0, 0, 0, 0, 1, 0])
    s = np.array([.9, .8, .7, .6, .5, .4, .3, .2, .1, .0])
    assert M.flag_top(s, 0.2).tolist() == [True, True] + [False] * 8
    assert M.precision_recall_at(y, s, 0.2) == (1.0, 0.5)
    assert M.precision_recall_at(y, s, 0.3) == (pytest.approx(2 / 3), 0.5)
    assert M.precision_recall_at(y, -s, 0.2) == (0.5, 0.25)
    assert M.flag_top(np.array([1, 1, 1, 1]), 0.5).tolist() == [True, True, False, False]   # ties: first come
    p, r = M.precision_recall(np.zeros(3), np.zeros(3))
    assert math.isnan(p) and math.isnan(r)


def test_flag_top_within_groups():
    s = np.array([9, 8, 7, 6, 5, 1, 2, 3, 4, 0])
    g = np.array(["a"] * 5 + ["b"] * 5)
    assert M.flag_top_within(s, g, 0.2).tolist() == [True, False, False, False, False, False, False, False, True, False]
    assert M.flag_top(s, 0.2).tolist() == [True, True] + [False] * 8                         # globally both are in group a


def test_brier_and_reliability():
    assert M.brier([1, 0], [0.8, 0.4]) == pytest.approx((0.04 + 0.16) / 2)
    y, p = [1, 1, 0, 0, 1], [0.95, 0.91, 0.12, 0.18, 1.0]
    curve = M.reliability_curve(y, p)
    assert [(r["bin"], r["n"]) for r in curve] == [(1, 2), (9, 3)]
    assert curve[0]["observed"] == 0.0 and curve[0]["mean_predicted"] == pytest.approx(0.15)
    assert curve[1]["observed"] == 1.0 and curve[1]["mean_predicted"] == pytest.approx((0.95 + 0.91 + 1.0) / 3)
    assert M.expected_calibration_error(y, p) == pytest.approx(0.4 * 0.15 + 0.6 * (1 - 2.86 / 3))
    assert M.expected_calibration_error([1, 0, 1, 0], [0.5, 0.5, 0.5, 0.5]) == 0.0


def test_bootstrap_interval_covers_a_known_mean():
    x = np.random.default_rng(3).normal(10, 2, 400)
    lo, hi = M.bootstrap_ci(lambda i: x[i].mean(), len(x), 400, seed=1)
    assert lo < x.mean() < hi and 0.25 < hi - lo < 0.55       # about 4 standard errors of 0.1
    assert M.bootstrap_ci(lambda i: x[i].mean(), len(x), 50, seed=1) == M.bootstrap_ci(lambda i: x[i].mean(), len(x), 50, seed=1)
    narrow = M.bootstrap_ci(lambda i: x[i].mean(), len(x), 400, seed=1, level=0.5)
    assert narrow[1] - narrow[0] < hi - lo


def test_paired_bootstrap_sign_and_null():
    rng = np.random.default_rng(5)
    y = rng.integers(0, 2, 600)
    good, noise = y + rng.normal(0, 0.5, 600), rng.normal(0, 1, 600)
    r = M.paired_bootstrap(M.average_precision, y, good, noise, 200, 1)
    assert r["difference"] > 0.2 and r["ci_low"] > 0 and r["interval_excludes_zero"] and r["share_of_resamples_above_zero"] == 1.0
    back = M.paired_bootstrap(M.average_precision, y, noise, good, 200, 1)
    assert back["difference"] == pytest.approx(-r["difference"]) and back["ci_high"] < 0
    same = M.paired_bootstrap(M.average_precision, y, good, good, 50, 1)
    assert same["difference"] == 0 and not same["interval_excludes_zero"]


def test_kaplan_meier_by_hand():
    # 5 students: leave on day 2, censored day 3, leave day 5, leave day 5, censored day 8
    curve = M.kaplan_meier([2, 3, 5, 5, 8], [1, 0, 1, 1, 0])
    assert [(r["day"], r["at_risk"], r["events"]) for r in curve] == [(2, 5, 1), (5, 3, 2)]
    assert curve[0]["survival"] == pytest.approx(0.8) and curve[1]["survival"] == pytest.approx(0.8 * (1 / 3))
    assert M.survival_at(curve, 1) == 1.0 and M.survival_at(curve, 2) == pytest.approx(0.8)
    assert M.survival_at(curve, 4.9) == pytest.approx(0.8) and M.survival_at(curve, 100) == pytest.approx(0.8 / 3)
    se = 0.8 * math.sqrt(1 / (5 * 4))
    assert curve[0]["ci_low"] == pytest.approx(0.8 - 1.96 * se) and curve[0]["ci_high"] == pytest.approx(min(1, 0.8 + 1.96 * se))
    # with no censoring it is the plain share remaining
    plain = M.kaplan_meier([1, 2, 3, 4], [1, 1, 1, 1])
    assert [r["survival"] for r in plain] == pytest.approx([0.75, 0.5, 0.25, 0.0])
    assert M.kaplan_meier([1, 2], [0, 0]) == []


def test_risk_ratio_and_gap():
    assert M.risk_ratio([1, 1, 0, 0, 1, 0], [1, 1, 1, 0, 0, 0]) == pytest.approx((2 / 3) / (1 / 3))
    assert math.isnan(M.risk_ratio([1, 0], [1, 1])) and math.isnan(M.risk_ratio([0, 0, 1], [0, 0, 1]))
    assert M.rate_gap({"a": 0.2, "b": 0.5, "c": float("nan"), "d": None}) == pytest.approx(0.3)
    assert math.isnan(M.rate_gap({}))


# --- model ---------------------------------------------------------------------------------------------------

def synthetic(n=1600, seed=0, signal=True):
    """Feature frame in the shape of gold.features_early_warning with a planted relation to the outcome."""
    rng = np.random.default_rng(seed)
    year = np.where(np.arange(n) < n // 2, 2013, 2014)
    engagement = rng.normal(0, 1, n)
    risk = 1 / (1 + np.exp(2.0 * engagement)) if signal else np.full(n, 0.45)
    y = (rng.random(n) < risk).astype(int)
    clicks = np.round(np.exp(3 + engagement + rng.normal(0, 0.3, n)))
    df = pd.DataFrame({"cutoff_day": 28, "sp_key": [f"k{i:05d}" for i in range(n)],
                       "student_key": [f"s{i:05d}" for i in range(n)], "mp_key": np.where(year == 2013, "AAA-2013J", "AAA-2014J"),
                       "code_module": "AAA", "code_presentation": np.where(year == 2013, "2013J", "2014J"),
                       "presentation_year": year, "at_risk": y, "final_result": np.where(y == 1, "Fail", "Pass")})
    for c in model.COUNTS:
        df[c] = rng.integers(0, 5, n).astype(float)
    df["clicks_total"], df["clicks_last_2w"] = clicks, np.round(clicks * 0.3)
    df["avg_score"] = np.where(rng.random(n) < 0.2, np.nan, np.clip(70 + 10 * engagement + rng.normal(0, 5, n), 0, 100))
    df["avg_days_late"] = np.where(np.isnan(df["avg_score"]), np.nan, rng.normal(0, 2, n))
    df["clicks_vs_cohort_median"] = clicks / np.median(clicks)
    df["highest_education"] = rng.choice(list(model.EDUCATION), n)
    df["gender"], df["disability"] = rng.choice(["M", "F"], n), rng.choice(["N", "Y"], n, p=[0.9, 0.1])
    df["imd_band"] = rng.choice(["0-10%", "40-50%", "90-100%", None], n)
    df["age_band"], df["region"] = rng.choice(["0-35", "35-55"], n), rng.choice(["North", "South"], n)
    return df


def test_split_keeps_each_student_on_one_side():
    df = model.prepare(synthetic(200))
    df.loc[df.index[:10], "student_key"] = df["student_key"].iloc[100:110].to_numpy()     # ten 2013 students return in 2014
    train, test, info = model.split(df)
    assert info == {"train_rows": 90, "test_rows": 100, "train_rows_dropped_for_overlap": 10,
                    "students_in_both_years": 10, "students_on_both_sides_after_split": 0}
    assert set(train["presentation_year"]) == {2013} and set(test["presentation_year"]) == {2014}
    assert not set(train["student_key"]) & set(test["student_key"])


def test_models_find_a_planted_signal_and_beat_chance():
    res, scored = model.run_cutoff(synthetic(), 28, n_boot=30)
    prev = res["models"]["gradient_boosting"]["prevalence"]
    for name in ("logistic_regression", "gradient_boosting", "rule_recent_clicks_ranking", "rule_previous_score"):
        assert res["models"][name]["pr_auc"] > prev + (0.1 if name == "rule_previous_score" else 0.2), name
    lr = res["models"]["logistic_regression"]
    assert lr["top20"]["contacted"] == 160 and lr["top20"]["precision"] > 0.8 and lr["brier"] < lr["brier_of_constant_prevalence"]
    assert lr["pr_auc_ci"][0] < lr["pr_auc"] < lr["pr_auc_ci"][1]
    assert res["best_baseline"] in ("logistic_regression", "rule_recent_clicks_ranking", "rule_previous_score")
    assert set(scored["cutoff_day"]) == {28} and len(scored) == 800 and scored["risk_score"].between(0, 1).all()
    assert scored["flag_top20_in_class"].sum() == 160


def test_canary_shuffled_labels_give_chance_level():
    df = synthetic(2400, seed=2)
    out = model.shuffled_label_canary(df, 28, repeats=3)
    assert abs(out["pr_auc_shuffled_labels_mean"] - out["prevalence"]) < 0.06
    real, _ = model.run_cutoff(df, 28, n_boot=10)
    assert real["models"]["gradient_boosting"]["pr_auc"] > out["pr_auc_shuffled_labels_max"] + 0.15


def test_no_signal_means_no_skill():
    res, _ = model.run_cutoff(synthetic(seed=4, signal=False), 28, n_boot=10)
    m = res["models"]["gradient_boosting"]
    assert abs(m["pr_auc"] - m["prevalence"]) < 0.08
    assert not res["gradient_boosting_vs_best_baseline"]["pr_auc"]["difference"] > 0.08


def test_protected_attributes_are_not_model_inputs():
    assert not set(model.FEATURES) & set(model.PROTECTED)
    assert not {"final_result", "at_risk", "date_unregistration", "student_key", "sp_key"} & set(model.FEATURES)
    df = model.prepare(synthetic(100))
    assert list(model.design(df).columns) == model.FEATURES
    wide = model.design(df, model.PROTECTED)
    assert any(c.startswith("gender_") for c in wide.columns) and any(c.startswith("imd_band_Not recorded") for c in wide.columns)
    again = model.design(df.head(5), model.PROTECTED, list(wide.columns))
    assert list(again.columns) == list(wide.columns) and not again.drop(columns=model.FEATURES).isna().any().any()


def test_baseline_rules():
    df = pd.DataFrame({"clicks_last_2w": [0, 3, 10], "avg_score": [np.nan, 40.0, np.nan], "n_due": [1, 1, 0]})
    assert model.rule_no_recent_clicks(df).tolist() == [1.0, 0.0, 0.0]
    assert model.rank_recent_clicks(df).tolist() == [0.0, -3.0, -10.0]
    assert model.rank_previous_score(df).tolist() == [100.0, 60.0, 50.0]


def test_reason_codes_name_what_pushes_the_score_up():
    df = model.prepare(synthetic())
    train, test, _ = model.split(df)
    lin = model.Linear().fit(model.design(train), train["at_risk"])
    x = model.design(test)
    contrib = lin.contributions(x)
    logit = contrib.sum(axis=1) + lin.model.intercept_[0]
    assert np.allclose(1 / (1 + np.exp(-logit)), lin.predict(x))            # contributions add up to the score
    low = x["clicks_total"].idxmin()
    reasons = lin.reasons(x.loc[[low]])[0]
    assert 1 <= len(reasons) <= 3 and any("click" in r for r in reasons)
    for r in lin.reasons(x.head(50)):
        assert all(isinstance(t, str) and "{" not in t and t for t in r)
    assert set(lin.columns) - set(model.FEATURES) == {"avg_score_missing", "avg_days_late_missing"}
    assert set(model.REASONS) >= set(lin.columns)


def test_fairness_slices_report_each_group():
    df = model.prepare(synthetic())
    test = df[df["presentation_year"] == 2014]
    score = -test["clicks_total"].to_numpy()
    f = model.fairness(test, score, n_boot=20)
    assert set(f) == {"gender", "disability", "age_band", "deprivation"}
    assert set(f["deprivation"]["groups"]) == {"Most deprived 30%", "Middle 40%", "Least deprived 30%", "Not recorded"}
    g = f["gender"]["groups"]
    assert g["M"]["n"] + g["F"]["n"] == len(test)
    assert f["gender"]["recall_gap"] == pytest.approx(abs(g["M"]["recall"] - g["F"]["recall"]))
    assert [model.band_imd(v) for v in ("0-10%", "20-30%", "30-40%", "60-70%", "70-80%", None, float("nan"))] == \
        ["Most deprived 30%", "Most deprived 30%", "Middle 40%", "Middle 40%", "Least deprived 30%", "Not recorded", "Not recorded"]
    # a score that flags one gender only shows up as a large gap
    biased = np.where(test["gender"] == "M", 1.0, 0.0) + 0.001 * score / abs(score).max()
    assert model.fairness(test, biased, n_boot=5)["gender"]["flag_rate_gap"] > 0.3


# --- clustering ----------------------------------------------------------------------------------------------

def blobs(k, n=300, spread=0.3, seed=0):
    rng = np.random.default_rng(seed)
    centres = rng.normal(0, 4, (k, 8))
    return np.vstack([c + rng.normal(0, spread, (n, 8)) for c in centres])


def test_stability_is_high_for_real_clusters_and_low_for_none():
    assert clustering.stability(blobs(3), 3, n_boot=5)["mean_ari"] > 0.98
    noise = np.random.default_rng(1).normal(0, 1, (900, 8))
    assert clustering.stability(noise, 5, n_boot=5)["mean_ari"] < 0.6


def test_choose_k_prefers_the_largest_stable_k_and_admits_instability():
    table = [{"k": 2, "mean_ari": 0.99, "min_ari": 0.97}, {"k": 3, "mean_ari": 0.93, "min_ari": 0.85},
             {"k": 4, "mean_ari": 0.67, "min_ari": 0.4}, {"k": 5, "mean_ari": 0.83, "min_ari": 0.68}]
    assert clustering.choose_k(table) == {"k": 3, "stable": True, "rule": clustering.choose_k(table)["rule"]}
    weak = [{"k": 2, "mean_ari": 0.5, "min_ari": 0.3}, {"k": 3, "mean_ari": 0.6, "min_ari": 0.2}]
    out = clustering.choose_k(weak)
    assert out["k"] == 3 and out["stable"] is False and "no k met" in out["rule"]
    edge = [{"k": 2, "mean_ari": 0.80, "min_ari": 0.80}, {"k": 3, "mean_ari": 0.9, "min_ari": 0.79}]
    assert clustering.choose_k(edge)["k"] == 2 and clustering.choose_k(edge)["stable"]
