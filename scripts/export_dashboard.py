"""Export the tables behind the dashboards from a finished lake to dashboard/data/*.csv.

Usage: python scripts/export_dashboard.py [--lake lake/full/lake] [--out dashboard/data]

Aggregate tables are read through the management access views, so small-cell suppression is applied before
anything leaves the lake. The only student-level table is StudentSnapshot (pseudonymous keys, presentations
that started in October 2014, the model's held-out year); it carries no protected attribute and no outcome.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from oulad import access  # noqa: E402
from oulad.lake import Lake  # noqa: E402

CURRENT = "2014J"
LAST_CURVE_WEEK = 33     # every presentation runs at least 33 full weeks, so nobody is censored before then
T = access.THRESHOLD


def tables(lake: Lake) -> dict[str, pd.DataFrame]:
    con = lake.connect()
    access.install(con, "export", "management")
    q = lambda sql: con.execute(sql).fetch_df()
    out = {}
    out["dim_module_presentation"] = q(f"""
        select mp_key as MpKey, code_module as Module, code_presentation as Presentation, presentation_year as Year,
               start_month as StartMonth, length_days as LengthDays,
               case when code_presentation = '{CURRENT}' then 'Yes' else 'No' end as IsCurrentTerm
        from gold.dim_module_presentation order by 1""")
    out["dim_week"] = q("select week_index as WeekIndex, week_label as WeekLabel, phase as Phase from gold.dim_week order by 1")
    scores = pd.read_parquet(lake.root / "exports" / "risk_scores.parquet")
    con.register("scores", scores)
    out["student_snapshot"] = q(f"""
        select f.sp_key as SpKey, f.mp_key as MpKey, f.cutoff_day as CutoffDay,
               cast(f.clicks_total as bigint) as ClicksTotal, cast(f.clicks_last_2w as bigint) as ClicksLast2w,
               cast(f.days_since_last_active as bigint) as DaysSinceLastActive,
               cast(f.n_due as bigint) as AssessmentsDue, cast(f.n_missed as bigint) as AssessmentsMissed,
               round(f.avg_score, 1) as AvgScore, round(s.risk_score, 3) as RiskScore,
               case when s.flag_top20_in_class then 1 else 0 end as Flagged,
               case when s.flag_top20_in_class then s.reason_1 else '' end as Reason1,
               case when s.flag_top20_in_class then s.reason_2 else '' end as Reason2,
               case when s.flag_top20_in_class then s.reason_3 else '' end as Reason3
        from gold.features_early_warning f join scores s using (sp_key, cutoff_day)
        where f.code_presentation = '{CURRENT}' order by 2, 3, 1""")
    out["agg_module_week"] = q(f"""
        with enrolled as (
            select d.mp_key, w.week_index, count(*) as n
            from gold.dim_student_presentation d join gold.dim_module_presentation m using (mp_key)
            join gold.dim_week w on w.week_index between 0 and m.last_week
            where d.date_registration is not null and d.date_registration <= w.day_to
              and (d.date_unregistration is null or d.date_unregistration > w.day_from)
            group by 1, 2)
        select e.mp_key as MpKey, e.week_index as WeekIndex,
               case when e.n >= {T} then e.n end as EnrolledStudents,
               case when e.n >= {T} then a.n_active_students end as ActiveStudents,
               case when e.n >= {T} then a.clicks end as Clicks
        from enrolled e left join access.mgmt_weekly_engagement a on a.mp_key = e.mp_key and a.week_index = e.week_index
        order by 1, 2""")
    out["agg_activity_week"] = q(f"""
        select f.mp_key as MpKey, f.week_index as WeekIndex, t.activity_group as ActivityGroup,
               case when count(distinct f.sp_key) >= {T} then sum(f.clicks) end as Clicks
        from gold.fact_engagement_weekly f join gold.dim_activity_type t using (activity_type)
        where f.week_index >= 0 group by 1, 2, 3 order by 1, 2, 3""")
    out["agg_outcome"] = q("""
        select mp_key as MpKey, n_students as Students, n_withdrawn as Withdrawn, n_fail as Failed, n_pass as Passed,
               case when suppressed then 1 else 0 end as Suppressed
        from access.mgmt_outcomes order by 1""")
    out["agg_outcome_by_group"] = q("""
        select mp_key as MpKey, dimension as Dimension, band as Band, n_students as Students, n_at_risk as AtRisk,
               case when n_at_risk is not null then n_students end as StudentsWithRate,
               case when suppressed then 1 else 0 end as Suppressed
        from access.mgmt_outcomes_by_group order by 1, 2, 3""")
    out["agg_withdrawal"] = q(f"""
        with followed as (
            select d.mp_key, d.date_unregistration
            from gold.dim_student_presentation d
            where d.date_unregistration is null or d.date_unregistration > 0),
        cell as (
            select f.mp_key, w.week_index, count(*) as n,
                   sum(case when f.date_unregistration <= w.day_to then 1 else 0 end) as n_left
            from followed f join gold.dim_week w on w.week_index between 0 and {LAST_CURVE_WEEK}
            group by 1, 2)
        select mp_key as MpKey, week_index as WeekIndex,
               case when n_left >= {T} and n - n_left >= {T} then n end as Followed,
               case when n_left >= {T} and n - n_left >= {T} then n_left end as LeftByWeekEnd
        from cell order by 1, 2""")
    out["ops_run_log"] = q("""
        select run_id as RunId, week as WeekIndex, status as Status, round(duration_s, 1) as DurationSeconds,
               bronze_rows_loaded as RowsLoaded, quarantine_rows as QuarantineRows, weeks_recomputed as WeeksRecomputed,
               n_checks as Checks, n_failed_error as FailedError, n_failed_warn as FailedWarn
        from ops.run_log order by started_at, run_id""")
    out["ops_checks"] = q("""
        select week as WeekIndex, kind as Kind, count(*) as Checks, sum(case when passed then 0 else 1 end) as Failed
        from ops.dq_results group by 1, 2 order by 1, 2""")
    out["ops_warnings"] = q("""
        select check_id as CheckId, severity as Severity, observed as Observed, detail as Detail
        from ops.dq_results where not passed and run_id = (select run_id from ops.run_log order by started_at desc, run_id desc limit 1)
        order by 1""")
    out["sec_lecturer_module"] = q("select app_user as AppUser, mp_key as MpKey from gold.sec_lecturer_assignment order by 1, 2")
    con.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lake", default=str(ROOT / "lake" / "full" / "lake"))
    ap.add_argument("--out", default=str(ROOT / "dashboard" / "data"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, df in tables(Lake(Path(a.lake))).items():
        for c in df.columns:      # whole numbers stay whole even when some cells are withheld
            if pd.api.types.is_float_dtype(df[c]) and c not in ("AvgScore", "RiskScore", "DurationSeconds"):
                df[c] = df[c].astype("Int64")
        df.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
        manifest[name] = {"rows": int(len(df)), "columns": list(df.columns),
                          "withheld_cells": int(df.isna().sum().sum()) if name.startswith("agg_") else None}
        print(f"{name:<26}{len(df):>8} rows {(out / (name + '.csv')).stat().st_size / 1e6:>7.2f} MB")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
