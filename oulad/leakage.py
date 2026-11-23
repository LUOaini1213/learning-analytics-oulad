"""A leakage detector for as-of features.

Idea: a feature computed "as of day D" must not change when everything that happened on or after day D is
altered. `perturb_future` rewrites the future in a copy of the gold tables (later clicks, later submissions,
later unregistrations, all final results); `leaking_columns` rebuilds the features on the altered copy and
returns every column whose value moved. An honest feature set returns an empty list.
"""
from __future__ import annotations

import duckdb

GOLD_INPUTS = ["dim_student_presentation", "dim_module_presentation", "dim_assessment", "dim_activity_type",
               "fact_student_week", "fact_engagement_weekly", "fact_assessment"]
KEYS = ["cutoff_day", "sp_key"]
TARGETS = ["final_result", "at_risk"]


def copy_gold(src: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """An independent in-memory copy of the gold tables the feature SQL reads."""
    dst = duckdb.connect()
    for schema in ("gold", "run"):
        dst.execute(f"create schema if not exists {schema}")
    src.execute("select 1").fetchall()      # finish any half-read result left on the connection
    for t in GOLD_INPUTS:
        dst.register("_t", src.sql(f"select * from gold.{t}").to_arrow_table())
        dst.execute(f"create table gold.{t} as select * from _t")
        dst.unregister("_t")
    return dst


def perturb_future(con: duckdb.DuckDBPyConnection, cutoff_day: int) -> None:
    """Change everything dated on or after `cutoff_day`, and every outcome, in place."""
    w = cutoff_day // 7
    con.execute(f"update gold.fact_student_week set clicks = clicks * 3 + 11, active_days = 1, "
                f"last_active_day = last_active_day + 1 where week_index >= {w}")
    con.execute(f"update gold.fact_engagement_weekly set clicks = clicks * 3 + 11, active_days = 1 "
                f"where week_index >= {w}")
    con.execute(f"update gold.fact_assessment set score = 100 - coalesce(score, 0), days_late = 99, "
                f"date_submitted = date_submitted + 5 where date_submitted >= {cutoff_day}")
    con.execute(f"update gold.dim_student_presentation set date_unregistration = date_unregistration + 13 "
                f"where date_unregistration >= {cutoff_day}")
    # students still enrolled at the cut-off get a made-up later withdrawal, and the other way round
    con.execute(f"update gold.dim_student_presentation set date_unregistration = {cutoff_day} + 20 "
                f"where date_unregistration is null and substr(sp_key, 1, 1) < '8'")
    con.execute("update gold.dim_student_presentation set "
                "final_result = case when substr(sp_key, 2, 1) < '8' then 'Withdrawn' else 'Pass' end, "
                "at_risk = case when substr(sp_key, 2, 1) < '8' then 1 else 0 end")
    con.execute(f"update gold.dim_assessment set due_day_effective = due_day_effective + 3, weight = 0 "
                f"where due_day_effective >= {cutoff_day}")


def _features(con, sql: str, cutoff_day: int):
    con.execute("create or replace table run.cutoffs (cutoff_day integer)")
    con.execute(f"insert into run.cutoffs values ({int(cutoff_day)})")
    return con.execute(f"select * from ({sql}) f order by sp_key").fetch_df()


def leaking_columns(con: duckdb.DuckDBPyConnection, sql: str, cutoff_day: int) -> list[str]:
    """Feature columns that change when the future is rewritten. Also reports a change in who is included."""
    before = _features(copy_gold(con), sql, cutoff_day)
    altered = copy_gold(con)
    perturb_future(altered, cutoff_day)
    after = _features(altered, sql, cutoff_day)
    if list(before["sp_key"]) != list(after["sp_key"]):
        return ["<population>"]
    out = []
    for c in before.columns:
        if c in KEYS + TARGETS:
            continue
        a, b = before[c], after[c]
        same = ((a == b) | (a.isna() & b.isna())).all()
        if not same:
            out.append(c)
    return out
