"""Early-warning models: who is at risk (Withdrawn or Fail), predicted at several cut-off days.

Train on presentations that started in 2013, test on those that started in 2014. A student who appears in
both years is removed from the training side, so nobody is on both sides of the split.
Baselines: two one-line rules and a logistic regression; then gradient boosting.
Protected attributes (gender, disability, deprivation band, age band, region) are not model inputs; an
ablation adds them to see what changes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression

from . import metrics as M

COUNTS = ["clicks_total", "clicks_last_2w", "clicks_prev_2w", "clicks_before_start", "active_days_total",
          "active_days_last_2w", "weeks_active", "days_since_last_active", "clicks_content",
          "clicks_communication", "clicks_quiz", "clicks_homepage", "n_due", "n_submitted", "n_banked",
          "n_missed", "n_late", "num_of_prev_attempts", "studied_credits", "registered_days_before_start"]
OTHER = ["avg_score", "avg_days_late", "clicks_vs_cohort_median", "education_level"]
FEATURES = COUNTS + OTHER
PROTECTED = ["gender", "disability", "imd_band", "age_band", "region"]
EDUCATION = {"No Formal quals": 0, "Lower Than A Level": 1, "A Level or Equivalent": 2, "HE Qualification": 3,
             "Post Graduate Qualification": 4}
LOGGED = [c for c in COUNTS if c not in ("days_since_last_active", "registered_days_before_start")]
TOP_FRACTIONS = [0.10, 0.20]
SEED = 20261123

# plain-language wording for the reason codes; {v} is the student's own value
REASONS = {
    "clicks_total": "few clicks so far ({v:.0f})",
    "clicks_last_2w": "little activity in the last two weeks ({v:.0f} clicks)",
    "clicks_prev_2w": "little activity in the two weeks before that ({v:.0f} clicks)",
    "clicks_before_start": "did not look at the site before the start ({v:.0f} clicks)",
    "active_days_total": "active on few days so far ({v:.0f})",
    "active_days_last_2w": "active on few days in the last two weeks ({v:.0f})",
    "weeks_active": "active in few weeks so far ({v:.0f})",
    "days_since_last_active": "{v:.0f} days since last seen online",
    "clicks_content": "little use of course content ({v:.0f} clicks)",
    "clicks_communication": "little use of forums ({v:.0f} clicks)",
    "clicks_quiz": "little use of quizzes ({v:.0f} clicks)",
    "clicks_homepage": "few visits to the module homepage ({v:.0f})",
    "n_due": "{v:.0f} assessment(s) already due",
    "n_submitted": "few assessments submitted ({v:.0f})",
    "n_banked": "{v:.0f} result(s) carried over from an earlier attempt",
    "n_missed": "{v:.0f} assessment(s) due but not submitted",
    "n_late": "{v:.0f} assessment(s) submitted late",
    "num_of_prev_attempts": "{v:.0f} earlier attempt(s) at this module",
    "studied_credits": "studying {v:.0f} credits in total",
    "registered_days_before_start": "registered {v:.0f} days before the start",
    "avg_score": "average score so far {v:.0f}",
    "avg_score_missing": "no assessment score yet",
    "avg_days_late": "submits {v:+.0f} days against the deadline on average",
    "avg_days_late_missing": "no submission to time yet",
    "clicks_vs_cohort_median": "clicks at {v:.1f} times the class median",
    "education_level": "prior education level {v:.0f} on a 0-4 scale",
}


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["education_level"] = df["highest_education"].map(EDUCATION).astype(float)
    df["at_risk"] = df["at_risk"].astype(int)
    return df


def split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Train = 2013 presentations, test = 2014; students seen in the test year are dropped from training."""
    test = df[df["presentation_year"] == 2014]
    train_all = df[df["presentation_year"] == 2013]
    overlap = set(train_all["student_key"]) & set(test["student_key"])
    train = train_all[~train_all["student_key"].isin(overlap)]
    info = {"train_rows": int(len(train)), "test_rows": int(len(test)),
            "train_rows_dropped_for_overlap": int(len(train_all) - len(train)),
            "students_in_both_years": int(len(overlap)),
            "students_on_both_sides_after_split": int(len(set(train["student_key"]) & set(test["student_key"])))}
    return train, test, info


def design(df: pd.DataFrame, extra: list[str] | None = None, columns: list[str] | None = None) -> pd.DataFrame:
    """Numeric matrix. Missing scores stay missing here; `impute` fills them for the linear model."""
    x = df[FEATURES].astype(float).copy()
    if extra:
        dummies = pd.get_dummies(df[extra].fillna("Not recorded"), prefix=extra, dtype=float)
        x = pd.concat([x, dummies], axis=1)
    if columns is not None:
        x = x.reindex(columns=columns, fill_value=0.0)
    return x


class Linear:
    """Logistic regression on log-scaled counts, standardised, with missing-value indicators.

    Kept transparent on purpose: the reason codes come from its per-feature contributions.
    """

    def fit(self, x: pd.DataFrame, y) -> "Linear":
        z = self._raw(x)
        self.columns = list(z.columns)
        self.fill = z.median().fillna(0.0)
        z = z.fillna(self.fill)
        self.mean, self.std = z.mean(), z.std(ddof=0).replace(0, 1.0)
        self.model = LogisticRegression(C=1.0, max_iter=2000).fit(((z - self.mean) / self.std).to_numpy(), np.asarray(y))
        return self

    @staticmethod
    def _raw(x: pd.DataFrame) -> pd.DataFrame:
        z = x.copy()
        for c in LOGGED:
            z[c] = np.log1p(z[c].clip(lower=0))
        for c in ("avg_score", "avg_days_late"):
            z[c + "_missing"] = z[c].isna().astype(float)
        return z

    def _z(self, x: pd.DataFrame) -> pd.DataFrame:
        z = self._raw(x).reindex(columns=self.columns).fillna(self.fill)
        return (z - self.mean) / self.std

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(self._z(x).to_numpy())[:, 1]

    def contributions(self, x: pd.DataFrame) -> pd.DataFrame:
        """coefficient x standardised value: how far each feature moves the log-odds from the average student."""
        return self._z(x) * self.model.coef_[0]

    def reasons(self, x: pd.DataFrame, top: int = 3) -> list[list[str]]:
        contrib = self.contributions(x)
        out = []
        for i in range(len(x)):
            row = contrib.iloc[i]
            names = [c for c in row.sort_values(ascending=False).index if row[c] > 0][:top]
            texts = []
            for c in names:
                base = c[: -len("_missing")] if c.endswith("_missing") else c
                v = x.iloc[i][base]
                if c.endswith("_missing") or pd.isna(v):
                    texts.append(REASONS[base + "_missing"])
                else:
                    texts.append(REASONS[c].format(v=float(v)))
            out.append(texts)
        return out


def boosted(x: pd.DataFrame, y) -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.06, max_leaf_nodes=15, l2_regularization=1.0,
                                          random_state=SEED).fit(x.to_numpy(), np.asarray(y))


def rule_no_recent_clicks(df: pd.DataFrame) -> np.ndarray:
    """Baseline rule: flag a student with no clicks in the last two weeks."""
    return (df["clicks_last_2w"].to_numpy() == 0).astype(float)


def rank_recent_clicks(df: pd.DataFrame) -> np.ndarray:
    """The same idea as a ranking: fewer clicks in the last two weeks = higher risk."""
    return -df["clicks_last_2w"].to_numpy(dtype=float)


def rank_previous_score(df: pd.DataFrame) -> np.ndarray:
    """Baseline rule: lower average score so far = higher risk. Something due but nothing scored ranks as
    the highest risk; nothing due yet ranks in the middle."""
    score = df["avg_score"].to_numpy(dtype=float)
    due = df["n_due"].to_numpy() > 0
    return np.where(np.isnan(score), np.where(due, 100.0, 50.0), 100.0 - score)


def evaluate(y, score, groups=None, prob: bool = False, n_boot: int = 300) -> dict:
    y, score = np.asarray(y), np.asarray(score, dtype=float)
    out = {"n": int(len(y)), "prevalence": float(y.mean()), "pr_auc": M.average_precision(y, score)}
    out["pr_auc_ci"] = list(M.bootstrap_ci(lambda i: M.average_precision(y[i], score[i]), len(y), n_boot, SEED))
    for f in TOP_FRACTIONS:
        p, r = M.precision_recall_at(y, score, f)
        key = f"top{int(f * 100)}"
        out[key] = {"contacted": M.top_k(len(y), f), "precision": p, "recall": r,
                    "recall_ci": list(M.bootstrap_ci(lambda i: M.precision_recall_at(y[i], score[i], f)[1], len(y), n_boot, SEED))}
        if groups is not None:
            pw, rw = M.precision_recall(y, M.flag_top_within(score, groups, f))
            out[key]["within_presentation"] = {"precision": pw, "recall": rw}
    if prob:
        out["brier"] = M.brier(y, score)
        out["brier_of_constant_prevalence"] = M.brier(y, np.full(len(y), y.mean()))
        out["expected_calibration_error"] = M.expected_calibration_error(y, score)
        out["reliability"] = M.reliability_curve(y, score)
    return out


def band_imd(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "Not recorded"
    low = int(str(v).split("-")[0])
    return "Most deprived 30%" if low < 30 else "Middle 40%" if low < 70 else "Least deprived 30%"


def fairness(test: pd.DataFrame, score: np.ndarray, fraction: float = 0.20, n_boot: int = 300) -> dict:
    """Per group: share flagged, and of the students who really were at risk, the share that was flagged."""
    y = test["at_risk"].to_numpy()
    flag = M.flag_top(score, fraction)
    slices = {"gender": test["gender"].to_numpy(), "disability": test["disability"].to_numpy(),
              "age_band": test["age_band"].to_numpy(),
              "deprivation": np.array([band_imd(v) for v in test["imd_band"]])}
    out = {}
    for name, g in slices.items():
        groups = {}
        for v in sorted(set(g)):
            m = g == v
            p, r = M.precision_recall(y[m], flag[m])
            groups[str(v)] = {"n": int(m.sum()), "at_risk_rate": float(y[m].mean()), "flag_rate": float(flag[m].mean()),
                              "recall": r, "precision": p}

        def gap(i, key="recall"):
            vals = {}
            for v in set(g):
                m = g[i] == v
                if m.sum() >= 30 and y[i][m].sum() > 0:
                    vals[v] = M.precision_recall(y[i][m], flag[i][m])[1]
            return M.rate_gap(vals)

        big = {k: v for k, v in groups.items() if v["n"] >= 30}
        out[name] = {"groups": groups, "recall_gap": M.rate_gap({k: v["recall"] for k, v in big.items()}),
                     "recall_gap_ci": list(M.bootstrap_ci(gap, len(y), n_boot, SEED)),
                     "flag_rate_gap": M.rate_gap({k: v["flag_rate"] for k, v in big.items()})}
    return out


def run_cutoff(df: pd.DataFrame, cutoff: int, n_boot: int = 300) -> tuple[dict, pd.DataFrame]:
    """Fit and evaluate everything for one cut-off day. Returns (results, scored test rows)."""
    d = prepare(df[df["cutoff_day"] == cutoff])
    train, test, info = split(d)
    y_tr, y_te = train["at_risk"].to_numpy(), test["at_risk"].to_numpy()
    groups = test["mp_key"].to_numpy()
    x_tr, x_te = design(train), design(test)
    linear = Linear().fit(x_tr, y_tr)
    gb = boosted(x_tr, y_tr)
    scores = {"rule_recent_clicks_ranking": rank_recent_clicks(test), "rule_previous_score": rank_previous_score(test),
              "logistic_regression": linear.predict(x_te), "gradient_boosting": gb.predict_proba(x_te.to_numpy())[:, 1]}
    res = {"cutoff_day": cutoff, "split": info, "models": {}}
    for name, s in scores.items():
        res["models"][name] = evaluate(y_te, s, groups, prob=name in ("logistic_regression", "gradient_boosting"), n_boot=n_boot)
    flag = rule_no_recent_clicks(test).astype(bool)
    p, r = M.precision_recall(y_te, flag)
    res["rule_no_clicks_in_last_two_weeks"] = {"flagged": int(flag.sum()), "flagged_share": float(flag.mean()),
                                                "precision": p, "recall": r}
    baselines = [m for m in scores if m != "gradient_boosting"]
    best = max(baselines, key=lambda m: res["models"][m]["pr_auc"])
    res["best_baseline"] = best
    res["gradient_boosting_vs_best_baseline"] = {
        "pr_auc": M.paired_bootstrap(M.average_precision, y_te, scores["gradient_boosting"], scores[best], n_boot, SEED),
        "recall_top20": M.paired_bootstrap(lambda yy, ss: M.precision_recall_at(yy, ss, 0.20)[1], y_te,
                                           scores["gradient_boosting"], scores[best], n_boot, SEED)}
    res["gradient_boosting_vs_logistic_regression"] = {
        "pr_auc": M.paired_bootstrap(M.average_precision, y_te, scores["gradient_boosting"], scores["logistic_regression"], n_boot, SEED)}
    res["fairness_gradient_boosting"] = fairness(test, scores["gradient_boosting"], n_boot=n_boot)
    res["fairness_logistic_regression"] = fairness(test, scores["logistic_regression"], n_boot=n_boot)
    # ablation: add the protected attributes as inputs
    cols = list(design(train, PROTECTED).columns)
    gb_p = boosted(design(train, PROTECTED, cols), y_tr)
    s_p = gb_p.predict_proba(design(test, PROTECTED, cols).to_numpy())[:, 1]
    res["ablation_with_protected_attributes"] = {
        "pr_auc": M.average_precision(y_te, s_p), "recall_top20": M.precision_recall_at(y_te, s_p, 0.20)[1],
        "vs_main_model_pr_auc": M.paired_bootstrap(M.average_precision, y_te, s_p, scores["gradient_boosting"], n_boot, SEED),
        "fairness_recall_gaps": {k: v["recall_gap"] for k, v in fairness(test, s_p, n_boot=20).items()}}
    by_result = {}
    flag20 = M.flag_top(scores["gradient_boosting"], 0.20)
    for outcome in ("Withdrawn", "Fail", "Pass", "Distinction"):
        m = test["final_result"].to_numpy() == outcome
        by_result[outcome] = {"n": int(m.sum()), "flagged_share": float(flag20[m].mean()) if m.any() else None}
    res["flagged_share_by_final_result_top20"] = by_result
    by_module = {}
    for mod in sorted(test["code_module"].unique()):
        m = test["code_module"].to_numpy() == mod
        by_module[mod] = {"n": int(m.sum()), "prevalence": float(y_te[m].mean()),
                          "pr_auc_gradient_boosting": M.average_precision(y_te[m], scores["gradient_boosting"][m]),
                          "pr_auc_logistic_regression": M.average_precision(y_te[m], scores["logistic_regression"][m]),
                          "in_training_data": bool(mod in set(train["code_module"]))}
    res["by_module"] = by_module
    coef = pd.Series(linear.model.coef_[0], index=linear.columns).sort_values()
    res["logistic_coefficients_standardised"] = {k: float(v) for k, v in coef.items()}
    scored = test[["sp_key", "mp_key", "code_module", "code_presentation", "final_result", "at_risk"]].copy()
    scored["cutoff_day"] = cutoff
    scored["risk_score"] = scores["logistic_regression"]
    scored["risk_score_gb"] = scores["gradient_boosting"]
    scored["flag_top20_in_class"] = M.flag_top_within(scores["logistic_regression"], groups, 0.20)
    reasons = linear.reasons(x_te)
    for k in range(3):
        scored[f"reason_{k + 1}"] = [r[k] if len(r) > k else "" for r in reasons]
    return res, scored


def shuffled_label_canary(df: pd.DataFrame, cutoff: int) -> dict:
    """Train on shuffled labels: the test PR-AUC must fall to the prevalence (chance)."""
    d = prepare(df[df["cutoff_day"] == cutoff])
    train, test, _ = split(d)
    rng = np.random.default_rng(SEED)
    y_shuffled = rng.permutation(train["at_risk"].to_numpy())
    gb = boosted(design(train), y_shuffled)
    s = gb.predict_proba(design(test).to_numpy())[:, 1]
    y = test["at_risk"].to_numpy()
    return {"cutoff_day": cutoff, "prevalence": float(y.mean()), "pr_auc_shuffled_labels": M.average_precision(y, s)}
