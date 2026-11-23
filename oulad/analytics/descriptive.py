"""Descriptive and diagnostic analysis on the gold tables. Everything here is an association in
observational data: none of it shows that one thing causes another."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import metrics as M

SEED = 20261123
KM_DAYS = [14, 28, 56, 84, 168]


def engagement_by_week(con) -> list[dict]:
    """Per week: students still registered at the start of the week, the share active, clicks per active student."""
    df = con.execute("""
        with enrolled as (
            select w.week_index, count(*) as n_enrolled
            from gold.dim_student_presentation d
            join gold.dim_module_presentation m using (mp_key)
            join gold.dim_week w on w.week_index between 0 and m.last_week
            where d.date_registration is not null and d.date_registration <= w.day_to
              and (d.date_unregistration is null or d.date_unregistration > w.day_from)
            group by 1),
        active as (
            select f.week_index, count(*) as n_active, sum(f.clicks) as clicks, median(f.clicks) as median_clicks,
                   avg(f.active_days) as mean_active_days
            from gold.fact_student_week f group by 1)
        select e.week_index, e.n_enrolled, a.n_active, a.clicks, a.median_clicks, a.mean_active_days
        from enrolled e join active a using (week_index) order by 1""").fetch_df()
    df["share_active"] = df["n_active"] / df["n_enrolled"]
    return [{k: (float(v) if k not in ("week_index", "n_enrolled", "n_active", "clicks") else int(v))
             for k, v in r.items()} for r in df.to_dict("records")]


def activity_mix(con) -> list[dict]:
    df = con.execute("""
        select t.activity_group, sum(f.clicks) as clicks
        from gold.fact_engagement_weekly f join gold.dim_activity_type t using (activity_type)
        group by 1 order by 2 desc""").fetch_df()
    df["share"] = df["clicks"] / df["clicks"].sum()
    return [{"activity_group": r["activity_group"], "clicks": int(r["clicks"]), "share": float(r["share"])}
            for r in df.to_dict("records")]


def assessment_timeliness(con) -> dict:
    by_type = con.execute("""
        select assessment_type, count(*) as n,
               avg(case when days_late > 0 then 1.0 else 0.0 end) as share_late,
               median(days_late) as median_days_late,
               quantile_cont(days_late, 0.9) as p90_days_late
        from gold.fact_assessment where is_banked = 0 and days_late is not null
        group by 1 order by 1""").fetch_df()
    # of the assessments that fell due while a student was registered, the share never submitted
    missed = con.execute("""
        with expected as (
            select d.sp_key, a.assessment_key, a.assessment_type
            from gold.dim_student_presentation d
            join gold.dim_assessment a using (mp_key)
            where a.assessment_type <> 'Exam' and d.date_registration is not null
              and d.date_registration <= a.due_day_effective
              and (d.date_unregistration is null or d.date_unregistration > a.due_day_effective))
        select e.assessment_type, count(*) as n_expected,
               sum(case when f.sp_key is null then 1 else 0 end) as n_not_submitted
        from expected e left join gold.fact_assessment f using (sp_key, assessment_key)
        group by 1 order by 1""").fetch_df()
    return {"by_type": [{"assessment_type": r["assessment_type"], "n": int(r["n"]), "share_late": float(r["share_late"]),
                         "median_days_late": float(r["median_days_late"]), "p90_days_late": float(r["p90_days_late"])}
                        for r in by_type.to_dict("records")],
            "not_submitted": [{"assessment_type": r["assessment_type"], "n_expected": int(r["n_expected"]),
                               "n_not_submitted": int(r["n_not_submitted"]),
                               "share": float(r["n_not_submitted"] / r["n_expected"])} for r in missed.to_dict("records")],
            "banked_results": int(con.execute("select count(*) from gold.fact_assessment where is_banked = 1").fetchone()[0])}


def withdrawal_frame(con) -> pd.DataFrame:
    """One row per student who was still registered on day 0, with the day they left or were last followed."""
    return con.execute("""
        select d.sp_key, m.code_module, m.mp_key, m.length_days,
               case when d.date_unregistration is not null and d.date_unregistration <= m.length_days
                    then d.date_unregistration else m.length_days end as day,
               case when d.date_unregistration is not null and d.date_unregistration <= m.length_days
                    then 1 else 0 end as left_module
        from gold.dim_student_presentation d join gold.dim_module_presentation m using (mp_key)
        where d.date_unregistration is null or d.date_unregistration > 0
        order by d.sp_key""").fetch_df()


def withdrawal(con) -> dict:
    counts = con.execute("""
        select count(*) as n,
               sum(case when date_unregistration <= 0 then 1 else 0 end) as left_on_or_before_day0,
               sum(case when date_unregistration > 0 then 1 else 0 end) as left_after_day0,
               sum(case when final_result = 'Withdrawn' and date_unregistration is null then 1 else 0 end) as withdrawn_without_date
        from gold.dim_student_presentation""").fetch_df().iloc[0]
    df = withdrawal_frame(con)
    curve = M.kaplan_meier(df["day"], df["left_module"])
    out = {"registrations": int(counts["n"]), "left_on_or_before_day_0": int(counts["left_on_or_before_day0"]),
           "left_after_day_0": int(counts["left_after_day0"]),
           "withdrawn_with_no_date": int(counts["withdrawn_without_date"]),
           "followed_from_day_0": int(len(df)),
           "still_registered": {str(d): M.survival_at(curve, d) for d in KM_DAYS},
           "still_registered_ci": {str(d): [next((r["ci_low"] for r in reversed(curve) if r["day"] <= d), 1.0),
                                            next((r["ci_high"] for r in reversed(curve) if r["day"] <= d), 1.0)]
                                   for d in KM_DAYS},
           "by_module": {}, "curve": [{"day": int(r["day"]), "survival": round(r["survival"], 5)} for r in curve
                                      if int(r["day"]) % 7 == 0]}
    for mod, g in df.groupby("code_module"):
        c = M.kaplan_meier(g["day"], g["left_module"])
        out["by_module"][mod] = {"n": int(len(g)), **{f"day_{d}": M.survival_at(c, d) for d in KM_DAYS}}
    # half of all departures after day 0 have happened by this day
    left = np.sort(df.loc[df["left_module"] == 1, "day"].to_numpy())
    out["median_day_of_leaving_among_leavers"] = float(np.median(left))
    return out


def associations(features: pd.DataFrame, n_boot: int = 500) -> list[dict]:
    """Associations between early behaviour (as of day 28) and the final outcome, with bootstrap intervals."""
    d = features[features["cutoff_day"] == 28]
    y = d["at_risk"].to_numpy(dtype=float)
    out = []

    def add(name: str, exposed: np.ndarray, note: str) -> None:
        exposed = np.asarray(exposed, dtype=bool)
        lo, hi = M.bootstrap_ci(lambda i: M.risk_ratio(y[i], exposed[i]), len(y), n_boot, SEED)
        out.append({"comparison": name, "n_in_group": int(exposed.sum()), "n_others": int((~exposed).sum()),
                    "at_risk_rate_in_group": float(y[exposed].mean()), "at_risk_rate_others": float(y[~exposed].mean()),
                    "risk_ratio": M.risk_ratio(y, exposed), "risk_ratio_ci": [lo, hi], "note": note})

    add("no clicks in the two weeks before day 28", d["clicks_last_2w"].to_numpy() == 0, "all students in the day-28 population")
    add("no clicks at all before day 28", d["clicks_total"].to_numpy() == 0, "all students in the day-28 population")
    add("did not open the site before the start", d["clicks_before_start"].to_numpy() == 0, "all students in the day-28 population")
    due = d["n_due"].to_numpy() > 0
    sub = d[due]
    y_keep, y = y, sub["at_risk"].to_numpy(dtype=float)
    add("an assessment was due before day 28 and was not submitted", sub["n_missed"].to_numpy() > 0,
        "students with at least one assessment due before day 28")
    y = y_keep
    add("has attempted this module before", d["num_of_prev_attempts"].to_numpy() > 0, "all students in the day-28 population")
    rho = float(pd.Series(d["clicks_total"]).corr(pd.Series(d["at_risk"]), method="spearman"))
    x, yy = d["clicks_total"].to_numpy(), d["at_risk"].to_numpy()
    lo, hi = M.bootstrap_ci(lambda i: float(pd.Series(x[i]).corr(pd.Series(yy[i]), method="spearman")), len(yy), 200, SEED)
    out.append({"comparison": "rank correlation between clicks before day 28 and being at risk",
                "spearman_rho": rho, "spearman_rho_ci": [lo, hi], "n": int(len(d))})
    return out


def outcomes(con) -> dict:
    df = con.execute("select final_result, count(*) as n from gold.dim_student_presentation group by 1").fetch_df()
    total = int(df["n"].sum())
    return {"n": total, **{r["final_result"]: {"n": int(r["n"]), "share": float(r["n"] / total)} for r in df.to_dict("records")},
            "at_risk_share": float(df.loc[df["final_result"].isin(["Withdrawn", "Fail"]), "n"].sum() / total)}
