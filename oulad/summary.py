"""Summaries of a finished replay: row counts by layer, reconciliation, data-quality findings, run timings,
and the check that the features snapshotted during term equal the ones rebuilt afterwards."""
from __future__ import annotations

import json
from pathlib import Path

from . import pipeline, transform
from .common import SOURCES
from .lake import Lake

FEATURE_ID = ["cutoff_day", "sp_key"]
LABEL_COLUMNS = ["final_result", "at_risk"]


def asof_equivalence(lake: Lake, con, cutoffs: list[int]) -> dict:
    """Compare gold.features_asof (written in the week of each cut-off, before any later data existed)
    with gold.features_early_warning (rebuilt at the end from the complete tables)."""
    cols = [r[0] for r in con.execute("describe gold.features_early_warning").fetchall()]
    feats = [c for c in cols if c not in FEATURE_ID + LABEL_COLUMNS]
    differ = " or ".join(f"a.{c} is distinct from b.{c}" for c in feats)
    out = {"feature_columns_compared": len(feats), "cutoffs": {}}
    for c in cutoffs:
        n_a, n_b, n_join, n_diff = con.execute(f"""
            select (select count(*) from gold.features_asof where cutoff_day = {c}),
                   (select count(*) from gold.features_early_warning where cutoff_day = {c}),
                   count(*), coalesce(sum(case when {differ} then 1 else 0 end), 0)
            from gold.features_asof a join gold.features_early_warning b using (cutoff_day, sp_key)
            where cutoff_day = {c}""").fetchone()
        out["cutoffs"][str(c)] = {"rows_snapshot": n_a, "rows_rebuilt": n_b, "rows_matched": n_join,
                                  "rows_with_any_difference": int(n_diff)}
    out["identical"] = all(v["rows_snapshot"] == v["rows_rebuilt"] == v["rows_matched"]
                           and v["rows_with_any_difference"] == 0 for v in out["cutoffs"].values())
    return out


def finalise(lake: Lake) -> dict:
    """Build the labelled feature table from the complete lake and compare it with the term-time snapshots."""
    con = lake.connect()
    transform.prepare(con)
    steps = transform.Steps("finalise")
    transform.build_features(lake, con, steps, pipeline.CUTOFFS, "gold.features_early_warning")
    eq = asof_equivalence(lake, con, pipeline.CUTOFFS)
    out = lake.root / "_events" / "lineage"
    out.mkdir(parents=True, exist_ok=True)
    (out / "finalise.json").write_text(json.dumps(steps.events, indent=1), encoding="utf-8")
    con.close()
    return eq


def pipeline_summary(lake: Lake, raw_dir: Path | None = None) -> dict:
    eq = finalise(lake)
    con = lake.connect()
    one = lambda sql: con.execute(sql).fetchone()[0]
    runs = pipeline.read_run_log(lake)
    layers = {t: one(f"select count(*) from {t}") for t in lake.tables() if not t.startswith("ops.")}
    last = runs[-1]["run_id"]
    dq = con.execute(f"select check_id, kind, severity, passed, observed, expected, detail from ops.dq_results "
                     f"where run_id = '{last}' order by check_id").fetchall()
    by_kind: dict[str, dict] = {}
    for _, kind, _, passed, *_ in dq:
        k = by_kind.setdefault(kind, {"checks": 0, "failed": 0})
        k["checks"] += 1
        k["failed"] += 0 if passed else 1
    status: dict[str, int] = {}
    for r in runs:
        status[r["status"]] = status.get(r["status"], 0) + 1
    vol = con.execute("select week, observed, passed from ops.dq_results where kind = 'volume' order by week").fetchall()
    steps_total: dict[str, float] = {}
    for r in runs:
        for s in json.loads(r["steps"]):
            steps_total[s["step"]] = round(steps_total.get(s["step"], 0.0) + s["seconds"], 3)
    source_rows = {}
    if raw_dir is not None:
        for name in SOURCES:
            with open(Path(raw_dir) / f"{name}.csv", "rb") as f:
                source_rows[name] = sum(1 for _ in f) - 1
    out = {
        "runs": {"n": len(runs), "by_status": status,
                 "total_seconds": round(sum(r["duration_s"] for r in runs), 1),
                 "median_seconds": sorted(r["duration_s"] for r in runs)[len(runs) // 2],
                 "max_seconds": max(r["duration_s"] for r in runs),
                 "first_week": min(r["week"] for r in runs), "last_week": max(r["week"] for r in runs)},
        "source_rows": source_rows,
        "layers": layers,
        "checks_last_run": {"n": len(dq), "failed_error": sum(1 for d in dq if not d[3] and d[2] == "error"),
                            "failed_warn": sum(1 for d in dq if not d[3] and d[2] == "warn"), "by_kind": by_kind},
        "checks_all_runs": {"n": one("select count(*) from ops.dq_results"),
                            "failed_error": one("select count(*) from ops.dq_results where not passed and severity = 'error'")},
        "warnings_last_run": [{"check_id": d[0], "observed": d[4], "detail": d[6]} for d in dq if not d[3]],
        "reconciliation": [{"check_id": d[0], "left": d[4], "right": d[5], "passed": d[3]} for d in dq
                           if d[1] == "reconciliation"],
        "volume": {"weeks_checked": len(vol), "weeks_flagged": [v[0] for v in vol if not v[2]],
                   "min_ratio": min((float(v[1]) for v in vol if v[1] != "n/a"), default=None),
                   "max_ratio": max((float(v[1]) for v in vol if v[1] != "n/a"), default=None)},
        "quarantine": {"rows": one("select count(*) from silver.quarantine"),
                       "by_reason": dict(con.execute("select reject_reason, count(*) from silver.quarantine "
                                                     "group by 1 order by 1").fetchall())},
        "clicks": {"source_rows": one("select count(*) from bronze.student_vle"),
                   "silver_rows": one("select count(*) from silver.vle_daily"),
                   "rows_folded_into_daily_totals": one("select count(*) from bronze.student_vle") -
                                                    one("select count(*) from silver.vle_daily"),
                   "total_clicks": one("select sum(sum_click) from silver.vle_daily"),
                   "rows_arriving_late": one("select count(*) from silver.vle_daily where last_batch_week > event_week")},
        "students": {"registrations": one("select count(*) from gold.dim_student_presentation"),
                     "distinct_students": one("select count(distinct student_key) from gold.dim_student_presentation"),
                     "students_in_more_than_one_presentation": one(
                         "select count(*) from (select student_key from gold.dim_student_presentation "
                         "group by 1 having count(*) > 1) t"),
                     "module_presentations": one("select count(*) from gold.dim_module_presentation"),
                     "modules": one("select count(distinct code_module) from gold.dim_module_presentation")},
        "asof_equivalence": eq,
        "seconds_by_step": dict(sorted(steps_total.items(), key=lambda kv: -kv[1])),
        "delta_versions": {t: lake.version(t) for t in lake.tables()},
    }
    con.close()
    return out
