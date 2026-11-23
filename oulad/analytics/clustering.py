"""Clusters of early engagement trajectories (weekly clicks in the first six weeks).

The number of clusters is chosen for stability first: a clustering that changes when the students are
resampled is not something to show a lecturer. For each k the students are resampled with replacement,
k-means is refitted, and its labels for the original students are compared with the reference labels by the
adjusted Rand index (ARI; 1 = identical grouping, 0 = no better than chance). Silhouette is reported beside it.
Clusters are fitted on 2013 presentations; 2014 students are assigned to the nearest centre.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score

from . import metrics as M

WEEKS = list(range(-2, 6))       # two weeks before the start to week 5
AS_OF_DAY = 42
STABLE_ARI = 0.80                # ASSUMPTION: below this ARI a clustering is called unstable
SEED = 20261123


def trajectories(con) -> pd.DataFrame:
    """log(1 + weekly clicks) for students registered before day 42 and still registered then, centred on
    the median trajectory of their own module-presentation (so course design does not define the clusters)."""
    pop = con.execute(f"""
        select d.sp_key, d.student_key, d.mp_key, m.presentation_year, d.at_risk, d.final_result
        from gold.dim_student_presentation d join gold.dim_module_presentation m using (mp_key)
        where d.date_registration is not null and d.date_registration < {AS_OF_DAY}
          and (d.date_unregistration is null or d.date_unregistration >= {AS_OF_DAY})""").fetch_df()
    clicks = con.execute(f"""
        select sp_key, week_index, clicks from gold.fact_student_week
        where week_index between {WEEKS[0]} and {WEEKS[-1]}""").fetch_df()
    wide = clicks.pivot(index="sp_key", columns="week_index", values="clicks").reindex(columns=WEEKS)
    wide = np.log1p(wide.reindex(pop["sp_key"]).fillna(0.0))
    wide.columns = [f"w{w}" for w in WEEKS]
    out = pd.concat([pop.reset_index(drop=True), wide.reset_index(drop=True)], axis=1)
    cols = list(wide.columns)
    out[[c + "_c" for c in cols]] = out[cols] - out.groupby("mp_key")[cols].transform("median")
    return out


def feature_columns() -> list[str]:
    return [f"w{w}_c" for w in WEEKS]


def fit(x: np.ndarray, k: int, seed: int = SEED) -> KMeans:
    return KMeans(n_clusters=k, n_init=5, random_state=seed).fit(x)


def stability(x: np.ndarray, k: int, n_boot: int = 20, seed: int = SEED) -> dict:
    """Mean ARI between the reference labels and labels from models refitted on resampled students."""
    ref = fit(x, k, seed).labels_
    rng = np.random.default_rng(seed)
    aris = []
    for b in range(n_boot):
        idx = rng.integers(0, len(x), len(x))
        aris.append(adjusted_rand_score(ref, fit(x[idx], k, seed + 1 + b).predict(x)))
    return {"mean_ari": float(np.mean(aris)), "min_ari": float(np.min(aris))}


def choose_k(table: list[dict]) -> dict:
    """Largest k that is stable both on average and in the worst resample; if none is, say so and fall back
    to the k with the highest mean ARI."""
    rule = f"largest k with mean ARI >= {STABLE_ARI} and worst-resample ARI >= {STABLE_ARI}"
    stable = [r for r in table if r["mean_ari"] >= STABLE_ARI and r["min_ari"] >= STABLE_ARI]
    if stable:
        return {"k": max(r["k"] for r in stable), "stable": True, "rule": rule}
    best = max(table, key=lambda r: r["mean_ari"])
    return {"k": best["k"], "stable": False, "rule": f"no k met: {rule}; most stable k shown"}


def run(con, ks=range(2, 8), n_boot: int = 20, sample: int = 6000) -> dict:
    df = trajectories(con)
    cols = feature_columns()
    train, test = df[df["presentation_year"] == 2013], df[df["presentation_year"] == 2014]
    x = train[cols].to_numpy()
    rng = np.random.default_rng(SEED)
    sub = x[rng.choice(len(x), min(sample, len(x)), replace=False)]
    table = []
    for k in ks:
        s = stability(sub, k, n_boot)
        labels = fit(sub, k).labels_
        table.append({"k": k, **s, "silhouette": float(silhouette_score(sub, labels))})
    choice = choose_k(table)
    model = fit(x, choice["k"])
    raw = [f"w{w}" for w in WEEKS]
    # order clusters by mean total early clicks, lowest first, so the numbering means something
    order = np.argsort([train.loc[model.labels_ == c, raw].sum(axis=1).mean() for c in range(choice["k"])])
    rank = {int(c): i for i, c in enumerate(order)}
    out = {"as_of_day": AS_OF_DAY, "weeks": WEEKS, "n_train": int(len(train)), "n_test": int(len(test)),
           "selection": table, "chosen": choice, "clusters": []}
    lab_tr = np.array([rank[int(c)] for c in model.labels_])
    lab_te = np.array([rank[int(c)] for c in model.predict(test[cols].to_numpy())])
    y_te = test["at_risk"].to_numpy(dtype=float)
    for c in range(choice["k"]):
        m_tr, m_te = lab_tr == c, lab_te == c
        ci = M.bootstrap_ci(lambda i: y_te[i][m_te[i]].mean() if m_te[i].any() else np.nan, len(y_te), 300, SEED)
        out["clusters"].append({
            "cluster": c, "n_train": int(m_tr.sum()), "share_train": float(m_tr.mean()),
            "mean_weekly_clicks_train": [float(np.expm1(train.loc[m_tr, r]).mean()) for r in raw],
            "at_risk_rate_train": float(train.loc[m_tr, "at_risk"].mean()),
            "n_test": int(m_te.sum()), "share_test": float(m_te.mean()),
            "at_risk_rate_test": float(y_te[m_te].mean()) if m_te.any() else None, "at_risk_rate_test_ci": list(ci)})
    rates = [c["at_risk_rate_test"] for c in out["clusters"] if c["at_risk_rate_test"] is not None]
    out["held_out_at_risk_rate_range"] = [float(min(rates)), float(max(rates))]
    out["held_out_overall_at_risk_rate"] = float(y_te.mean())
    return out
