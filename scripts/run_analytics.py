"""Run the descriptive analysis, the clustering and the early-warning models on a finished lake.

Usage: python scripts/run_analytics.py [--lake lake/full/lake] [--out results] [--fast]
Writes descriptive.json, clustering.json and model.json to --out, and the scored 2014 students (used by the
dashboard) to <lake root>/exports/risk_scores.parquet.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from oulad import leakage, transform  # noqa: E402
from oulad.analytics import clustering, descriptive, model  # noqa: E402
from oulad.lake import Lake  # noqa: E402
from oulad.pipeline import CUTOFFS  # noqa: E402


def run(lake: Lake, out: Path, fast: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    con = lake.connect()
    # a fixed row order: tree models and resampling depend on it, and Delta files carry no order of their own
    features = con.execute("select * from gold.features_early_warning order by cutoff_day, sp_key").fetch_df()
    n_boot = 40 if fast else 300
    desc = {"outcomes": descriptive.outcomes(con), "engagement_by_week": descriptive.engagement_by_week(con),
            "activity_mix": descriptive.activity_mix(con), "assessment_timeliness": descriptive.assessment_timeliness(con),
            "withdrawal": descriptive.withdrawal(con),
            "associations_day_28": descriptive.associations(features, 60 if fast else 500)}
    (out / "descriptive.json").write_text(json.dumps(desc, indent=1), encoding="utf-8")
    clus = clustering.run(con, ks=range(2, 5) if fast else range(2, 8), n_boot=4 if fast else 20,
                          sample=600 if fast else 6000)
    (out / "clustering.json").write_text(json.dumps(clus, indent=1), encoding="utf-8")
    res, scored = {"cutoffs": {}, "features": model.FEATURES, "protected_attributes_not_used": model.PROTECTED}, []
    for c in CUTOFFS:
        r, s = model.run_cutoff(features, c, n_boot)
        res["cutoffs"][str(c)] = r
        scored.append(s)
        print(f"day {c}: " + ", ".join(f"{k} {v['pr_auc']:.3f}" for k, v in r["models"].items()), flush=True)
    res["canary_shuffled_labels"] = model.shuffled_label_canary(features, 28)
    res["ablation_max_abs_pr_auc_difference"] = max(
        abs(r["ablation_with_protected_attributes"]["vs_main_model_pr_auc"]["difference"]) for r in res["cutoffs"].values())
    # rewrite everything dated on or after each cut-off and rebuild the features: any column that moves is a leak
    res["leakage_detector"] = {str(c): leakage.leaking_columns(con, transform.features_sql(), c) for c in CUTOFFS}
    (out / "model.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    exports = lake.root / "exports"
    exports.mkdir(exist_ok=True)
    pd.concat(scored).to_parquet(exports / "risk_scores.parquet", index=False)
    con.close()
    return {"descriptive": desc, "clustering": clus, "model": res}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default=str(ROOT / "lake" / "full" / "lake"))
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--fast", action="store_true")
    a = ap.parse_args()
    run(Lake(Path(a.lake)), Path(a.out), a.fast)
