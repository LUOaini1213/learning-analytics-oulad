"""Evaluation metrics, written out in full so each can be tested against a hand-worked case.

Convention: y is 1 for an at-risk student (Withdrawn or Fail), score is higher for higher risk.
"""
from __future__ import annotations

import math

import numpy as np


def _order(score: np.ndarray) -> np.ndarray:
    """Highest score first; ties keep their original order (stable), so results are reproducible."""
    return np.argsort(-np.asarray(score, dtype=float), kind="stable")


def average_precision(y, score) -> float:
    """Area under the precision-recall curve as the mean of precision at each positive, with tied scores
    treated as one threshold (the same definition scikit-learn uses)."""
    y, score = np.asarray(y, dtype=float), np.asarray(score, dtype=float)
    n_pos = y.sum()
    if n_pos == 0:
        return float("nan")
    order = _order(score)
    y, score = y[order], score[order]
    tp = np.cumsum(y)
    last_of_tie = np.r_[score[1:] != score[:-1], True]
    precision = tp[last_of_tie] / (np.flatnonzero(last_of_tie) + 1)
    recall = tp[last_of_tie] / n_pos
    return float(np.sum(np.diff(np.r_[0.0, recall]) * precision))


def top_k(n: int, fraction: float) -> int:
    """Number of students that can be contacted: the fraction of the cohort, rounded up, at least one."""
    return max(1, math.ceil(fraction * n - 1e-9))


def flag_top(score, fraction: float) -> np.ndarray:
    """Boolean mask of the `fraction` of students with the highest scores."""
    score = np.asarray(score, dtype=float)
    flag = np.zeros(len(score), dtype=bool)
    flag[_order(score)[: top_k(len(score), fraction)]] = True
    return flag


def flag_top_within(score, groups, fraction: float) -> np.ndarray:
    """The same, applied separately inside each group (each lecturer contacts a share of their own class)."""
    score, groups = np.asarray(score, dtype=float), np.asarray(groups)
    flag = np.zeros(len(score), dtype=bool)
    for g in np.unique(groups):
        idx = np.flatnonzero(groups == g)
        flag[idx[flag_top(score[idx], fraction)]] = True
    return flag


def precision_recall(y, flag) -> tuple[float, float]:
    y, flag = np.asarray(y).astype(bool), np.asarray(flag).astype(bool)
    tp = float(np.sum(y & flag))
    precision = tp / flag.sum() if flag.sum() else float("nan")
    recall = tp / y.sum() if y.sum() else float("nan")
    return precision, recall


def precision_recall_at(y, score, fraction: float) -> tuple[float, float]:
    return precision_recall(y, flag_top(score, fraction))


def brier(y, prob) -> float:
    y, prob = np.asarray(y, dtype=float), np.asarray(prob, dtype=float)
    return float(np.mean((prob - y) ** 2))


def reliability_curve(y, prob, bins: int = 10) -> list[dict]:
    """Equal-width probability bins: mean predicted probability against the observed at-risk share."""
    y, prob = np.asarray(y, dtype=float), np.asarray(prob, dtype=float)
    which = np.minimum((prob * bins).astype(int), bins - 1)
    out = []
    for b in range(bins):
        m = which == b
        if m.any():
            out.append({"bin": b, "n": int(m.sum()), "mean_predicted": float(prob[m].mean()),
                        "observed": float(y[m].mean())})
    return out


def expected_calibration_error(y, prob, bins: int = 10) -> float:
    n = len(np.asarray(y))
    return float(sum(r["n"] / n * abs(r["mean_predicted"] - r["observed"]) for r in reliability_curve(y, prob, bins)))


def bootstrap_ci(stat, n: int, n_boot: int = 500, seed: int = 0, level: float = 0.95) -> tuple[float, float]:
    """Percentile interval for stat(index array) over resamples of n rows with replacement."""
    rng = np.random.default_rng(seed)
    vals = np.array([stat(rng.integers(0, n, n)) for _ in range(n_boot)], dtype=float)
    vals = vals[~np.isnan(vals)]
    lo, hi = np.quantile(vals, [(1 - level) / 2, 1 - (1 - level) / 2])
    return float(lo), float(hi)


def paired_bootstrap(metric, y, score_a, score_b, n_boot: int = 500, seed: int = 0) -> dict:
    """metric(y, score_a) - metric(y, score_b) on the same resampled students each time."""
    y, a, b = np.asarray(y), np.asarray(score_a), np.asarray(score_b)
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(n_boot):
        i = rng.integers(0, len(y), len(y))
        diffs.append(metric(y[i], a[i]) - metric(y[i], b[i]))
    diffs = np.array(diffs, dtype=float)
    lo, hi = np.quantile(diffs, [0.025, 0.975])
    return {"difference": float(metric(y, a) - metric(y, b)), "ci_low": float(lo), "ci_high": float(hi),
            "share_of_resamples_above_zero": float(np.mean(diffs > 0)),
            "interval_excludes_zero": bool(lo > 0 or hi < 0)}


def kaplan_meier(time, event) -> list[dict]:
    """Product-limit estimate. `time` is the day the student left or was last seen, `event` 1 if they left.

    Returns one row per distinct event day: at_risk, events, survival, and a 95% interval from Greenwood's
    formula on the plain scale, clipped to [0, 1].
    """
    time, event = np.asarray(time, dtype=float), np.asarray(event).astype(bool)
    out, s, var_sum = [], 1.0, 0.0
    for t in np.unique(time[event]):
        at_risk = int(np.sum(time >= t))
        d = int(np.sum((time == t) & event))
        s *= 1 - d / at_risk
        if at_risk > d:
            var_sum += d / (at_risk * (at_risk - d))
        se = s * math.sqrt(var_sum)
        out.append({"day": float(t), "at_risk": at_risk, "events": d, "survival": s,
                    "ci_low": max(0.0, s - 1.96 * se), "ci_high": min(1.0, s + 1.96 * se)})
    return out


def survival_at(curve: list[dict], day: float) -> float:
    """Share still registered just after `day` (step function; 1.0 before the first event)."""
    s = 1.0
    for row in curve:
        if row["day"] <= day:
            s = row["survival"]
    return s


def risk_ratio(y, exposed) -> float:
    y, exposed = np.asarray(y, dtype=float), np.asarray(exposed).astype(bool)
    if exposed.all() or not exposed.any() or y[~exposed].mean() == 0:
        return float("nan")
    return float(y[exposed].mean() / y[~exposed].mean())


def rate_gap(values: dict[str, float]) -> float:
    """Largest minus smallest value across groups (ignoring groups with no value)."""
    v = [x for x in values.values() if x is not None and not math.isnan(x)]
    return float(max(v) - min(v)) if v else float("nan")
