"""Run the SQL transformations: bronze -> silver (with quarantine) -> gold, and the feature tables.

Every transformation is one SELECT in sql/. This module only decides the order, which partitions to
recompute, and where to write; it also records a lineage event and a timing for each step.
"""
from __future__ import annotations

import time

from .common import FIRST_WEEK, LAST_WEEK, SOURCES, SQL
from .lake import Lake
from .lineage import referenced_tables, run_event

# (silver table, stage view, bronze source it is staged from)
SILVER_KEYED = [("courses", "courses"), ("assessments", "assessments"), ("vle_sites", "vle_sites"),
                ("student_registration", "student_registration"), ("student_info", "student_info"),
                ("student_assessment", "student_assessment")]
GOLD_FULL = ["dim_module_presentation", "dim_week", "dim_activity_type", "dim_assessment",
             "dim_student_presentation", "sec_lecturer_assignment", "fact_assessment"]
GOLD_BY_WEEK = ["fact_student_week", "fact_engagement_weekly"]
EVENT_WEEK = "cast(floor(try_cast(nullif(trim(date), '?') as integer) / 7.0) as integer)"


def read_sql(*parts: str) -> str:
    return (SQL.joinpath(*parts)).read_text(encoding="utf-8")


def stage_sql() -> dict[str, str]:
    return {p.stem[len("stage_"):]: p.read_text(encoding="utf-8") for p in sorted((SQL / "silver").glob("stage_*.sql"))}


class Steps:
    """Collects timings and lineage events for one run."""

    def __init__(self, run_id: str):
        self.run_id, self.steps, self.events = run_id, [], []
        self._stage = stage_sql()

    def materialise(self, lake: Lake, con, target: str, sql: str, partition_by=None, predicate=None,
                    extra_inputs: list[str] | None = None) -> int:
        t0 = time.perf_counter()
        table = con.sql(sql).arrow().read_all()
        lake.write(target, table, partition_by=partition_by, predicate=predicate)
        lake.refresh(con, [target])
        seconds = time.perf_counter() - t0
        fields = [(f.name, str(f.type)) for f in table.schema]
        inputs = referenced_tables(sql, self._stage) + (extra_inputs or [])
        self.steps.append({"step": target, "seconds": round(seconds, 3), "rows_written": table.num_rows})
        self.events.append(run_event(self.run_id, target, inputs, target, fields, table.num_rows))
        return table.num_rows


def prepare(con, salt_from_variable: bool = True) -> None:
    """Session tables that the SQL files read: the salt, the week calendar and the cut-offs."""
    con.execute("create schema if not exists stage")
    con.execute("create schema if not exists run")
    con.execute("create or replace table run.settings as select getvariable('pseudonym_salt') as pseudonym_salt")
    con.execute(f"create or replace table run.weeks as select cast(w as integer) as week "
                f"from range({FIRST_WEEK}, {LAST_WEEK + 1}) t(w)")
    con.execute("create table if not exists run.affected_weeks (week integer)")
    con.execute("create table if not exists run.cutoffs (cutoff_day integer)")


def set_weeks(con, table: str, weeks: list[int]) -> None:
    col = "cutoff_day" if table == "cutoffs" else "week"
    con.execute(f"create or replace table run.{table} ({col} integer)")
    if weeks:
        con.execute(f"insert into run.{table} values " + ", ".join(f"({int(w)})" for w in weeks))


def _weeks(con, sql: str) -> set[int]:
    return {r[0] for r in con.execute(sql).fetchall() if r[0] is not None}


def run_silver(lake: Lake, con, steps: Steps, loaded_weeks: list[int], full_refresh: bool = False) -> list[int]:
    """Rebuild the keyed silver tables, the quarantine, and the affected weeks of silver.vle_daily.

    Returns the list of event weeks that were recomputed.
    """
    stages = stage_sql()
    for table, stage in SILVER_KEYED:
        con.execute(f"create or replace view stage.{stage} as {stages[stage]}")
        steps.materialise(lake, con, f"silver.{table}", read_sql("silver", f"{table}.sql"))
    con.execute(f"create or replace view stage.student_vle as {stages['student_vle']}")
    # weeks to recompute: those touched by the new deliveries, plus any week that had, or now has,
    # quarantined click rows (a row rejected as an orphan is released once its student or site arrives)
    affected: set[int] = set()
    if lake.exists("silver.quarantine"):
        affected |= _weeks(con, "select distinct event_week from silver.quarantine where source_table = 'student_vle'")
    steps.materialise(lake, con, "silver.quarantine", read_sql("silver", "quarantine.sql"))
    affected |= _weeks(con, "select distinct event_week from silver.quarantine where source_table = 'student_vle'")
    if full_refresh or not lake.exists("silver.vle_daily"):
        affected |= _weeks(con, f"select distinct {EVENT_WEEK} from bronze.student_vle")
        if lake.exists("silver.vle_daily"):
            affected |= _weeks(con, "select distinct event_week from silver.vle_daily")
    elif loaded_weeks:
        weeks = ", ".join(str(int(w)) for w in loaded_weeks)
        affected |= _weeks(con, f"select distinct {EVENT_WEEK} from bronze.student_vle where _batch_week in ({weeks})")
    weeks = sorted(affected)
    set_weeks(con, "affected_weeks", weeks)
    if weeks or not lake.exists("silver.vle_daily"):
        predicate = f"event_week in ({', '.join(map(str, weeks))})" if weeks else None
        steps.materialise(lake, con, "silver.vle_daily", read_sql("silver", "vle_daily.sql"),
                          partition_by=["event_week"], predicate=predicate)
    return weeks


def run_gold(lake: Lake, con, steps: Steps, weeks: list[int]) -> None:
    for table in GOLD_FULL:
        steps.materialise(lake, con, f"gold.{table}", read_sql("gold", f"{table}.sql"))
    set_weeks(con, "affected_weeks", weeks)
    for table in GOLD_BY_WEEK:
        if weeks or not lake.exists(f"gold.{table}"):
            predicate = f"week_index in ({', '.join(map(str, weeks))})" if weeks else None
            steps.materialise(lake, con, f"gold.{table}", read_sql("gold", f"{table}.sql"),
                              partition_by=["week_index"], predicate=predicate)


def features_sql() -> str:
    return read_sql("features", "features_early_warning.sql")


def build_features(lake: Lake, con, steps: Steps, cutoffs: list[int], target: str) -> int:
    """Write the feature table for the given cut-off days (one partition per cut-off)."""
    bad = [c for c in cutoffs if c % 7 != 0 or c <= 0]
    if bad:
        raise ValueError(f"cut-off days must be positive multiples of 7, got {bad}")
    set_weeks(con, "cutoffs", cutoffs)
    predicate = f"cutoff_day in ({', '.join(map(str, cutoffs))})"
    return steps.materialise(lake, con, target, features_sql(), partition_by=["cutoff_day"], predicate=predicate)


def bronze_sources() -> list[str]:
    return [f"bronze.{t}" for t, _ in SOURCES.values()]
