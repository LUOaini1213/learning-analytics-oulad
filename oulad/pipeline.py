"""The weekly load: ingest gate -> bronze -> silver -> gold -> contract checks -> publish or roll back.

Outcomes of one run (`status` in ops.run_log):
  SUCCESS                 every check passed
  SUCCESS_WITH_WARNINGS   only warn-severity checks failed
  REJECTED_AT_INGEST      the delivery failed the ingest gate; nothing was written
  ROLLED_BACK             an error-severity check failed after the transformations; silver and gold were
                          restored to their versions from before the run (Delta `restore`), bronze keeps the
                          delivery as landed so it can be investigated
Each run appends one row to ops.run_log, one row per check to ops.dq_results, and writes its lineage events
to <lake>/_events/lineage/<run_id>.json.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import time
from pathlib import Path

import pyarrow as pa

from . import bronze, contracts as dq, transform
from .common import SOURCES, as_of_day
from .lake import Lake
from .lineage import run_event

CUTOFFS = [14, 28, 56, 84]
QUARANTINE_ERROR_SHARE = 0.05   # ASSUMPTION: more than 5% of a delivery rejected means the delivery is bad

RUN_LOG_SCHEMA = pa.schema([
    ("run_id", pa.string()), ("week", pa.int32()), ("as_of_day", pa.int32()), ("status", pa.string()),
    ("started_at", pa.string()), ("finished_at", pa.string()), ("duration_s", pa.float64()),
    ("bronze_rows_loaded", pa.int64()), ("quarantine_rows", pa.int64()), ("weeks_recomputed", pa.int32()),
    ("n_checks", pa.int32()), ("n_failed_error", pa.int32()), ("n_failed_warn", pa.int32()),
    ("failures", pa.string()), ("steps", pa.string()), ("table_versions", pa.string())])
DQ_SCHEMA = pa.schema([
    ("run_id", pa.string()), ("week", pa.int32()), ("check_id", pa.string()), ("table", pa.string()),
    ("kind", pa.string()), ("severity", pa.string()), ("passed", pa.bool_()), ("observed", pa.string()),
    ("expected", pa.string()), ("detail", pa.string())])


def _now() -> str:
    # microseconds: on a fast machine several weekly runs start within the same second
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _append(lake: Lake, name: str, rows: list[dict], schema: pa.Schema) -> None:
    if rows:
        lake.write(name, pa.Table.from_pylist(rows, schema=schema), mode="append")
        if lake.version(name) % 10 == 9:
            lake.compact(name)


def read_run_log(lake: Lake) -> list[dict]:
    if not lake.exists("ops.run_log"):
        return []
    rows = lake.table("ops.run_log").to_pyarrow_table().to_pylist()
    # ties are broken by week and attempt as numbers: run_id sorts as text, which puts week -3 before week -5
    return sorted(rows, key=lambda r: (r["started_at"], r["week"], int(r["run_id"].rsplit("attempt", 1)[1])))


def quarantine_checks(con, week: int) -> list[dict]:
    """Two checks on rejected rows: any at all is a warning; a large share of this week's delivery is an error."""
    delivered = sum(con.execute(f"select count(*) from bronze.{t} where _batch_week = {week}").fetchone()[0]
                    for t, _ in SOURCES.values())
    rejected = con.execute(f"select count(*) from silver.quarantine where batch_week = {week}").fetchone()[0]
    total = con.execute("select count(*) from silver.quarantine").fetchone()[0]
    share = rejected / delivered if delivered else 0.0
    base = {"table": "silver.quarantine", "kind": "quarantine"}
    return [
        {**base, "check_id": "quarantine_is_empty", "severity": "warn", "passed": total == 0,
         "observed": str(total), "expected": "0", "detail": "rejected rows waiting in silver.quarantine"},
        {**base, "check_id": "quarantine_share_of_delivery", "severity": "error",
         "passed": share <= QUARANTINE_ERROR_SHARE, "observed": f"{share:.4f}",
         "expected": f"<= {QUARANTINE_ERROR_SHARE}", "detail": f"{rejected} of {delivered} delivered rows rejected"}]


def unresolved_rollbacks(run_log: list[dict], week: int) -> list[int]:
    """Earlier weeks whose most recent run was rolled back: later weeks must wait until they are fixed."""
    last: dict[int, str] = {}
    for r in run_log:
        last[r["week"]] = r["status"]
    return sorted(w for w, status in last.items() if w < week and status == "ROLLED_BACK")


def _restore(lake: Lake, before: dict[str, int]) -> list[str]:
    """Put silver and gold back to the versions they had before the run."""
    restored = []
    for name in lake.tables():
        if name.split(".")[0] not in ("silver", "gold"):
            continue
        if name not in before:
            shutil.rmtree(lake.path(name))
            lake.forget(name)
            restored.append(name)
        elif lake.version(name) != before[name]:
            lake.table(name).restore(before[name])
            lake.forget(name)
            restored.append(name)
    return restored


def run_week(lake: Lake, landing: Path, week: int, contracts: dict | None = None,
             full_refresh: bool = False) -> dict:
    contracts = contracts or dq.load_contracts()
    bronze.init_lake(lake)
    run_log = read_run_log(lake)
    attempt = 1 + sum(1 for r in run_log if r["week"] == week)
    run_id = f"week{week:+03d}-attempt{attempt}"
    started, t0 = _now(), time.perf_counter()
    record = {"run_id": run_id, "week": week, "as_of_day": as_of_day(week), "started_at": started,
              "bronze_rows_loaded": 0, "quarantine_rows": 0, "weeks_recomputed": 0, "n_checks": 0,
              "n_failed_error": 0, "n_failed_warn": 0}
    steps = transform.Steps(run_id)
    results: list[dict] = []
    before = lake.versions()
    try:
        t = time.perf_counter()
        blocked = unresolved_rollbacks(run_log, week)
        if blocked:
            raise bronze.IngestError([{"check": "unresolved_rollback",
                                       "detail": f"week(s) {blocked} were rolled back and must be re-run first"}])
        ingest = bronze.ingest_week(lake, Path(landing), week, run_id)
        steps.steps.append({"step": "bronze.ingest", "seconds": round(time.perf_counter() - t, 3),
                            "rows_written": sum(ingest["loaded"].values())})
        for name, n in ingest["loaded"].items():
            target = f"bronze.{SOURCES[name][0]}"
            fields = [(f.name, str(f.type)) for f in bronze.bronze_schema(name)]
            steps.events.append(run_event(run_id, target, [f"landing.{name}"], target, fields, n))
    except bronze.IngestError as e:
        results = [{"check_id": f"ingest_gate:{f['check']}", "table": "landing", "kind": "ingest_gate",
                    "severity": "error", "passed": False, "observed": f["detail"], "expected": "clean delivery",
                    "detail": f["detail"]} for f in e.failures]
        return _finish(lake, record, steps, results, "REJECTED_AT_INGEST", t0, e.failures)
    record["bronze_rows_loaded"] = sum(ingest["loaded"].values())
    results += [{"check_id": f"ingest_gate:{w['check']}", "table": "landing", "kind": "ingest_gate",
                 "severity": "warn", "passed": False, "observed": w["detail"], "expected": "no drift",
                 "detail": w["detail"]} for w in ingest["warnings"]]
    con = lake.connect()
    transform.prepare(con)
    weeks = transform.run_silver(lake, con, steps, [week], full_refresh=full_refresh or bool(ingest["replaced"]))
    transform.run_gold(lake, con, steps, weeks)
    if 7 * (week + 1) in CUTOFFS:   # the snapshot a lecturer would have been given this week
        transform.build_features(lake, con, steps, [7 * (week + 1)], "gold.features_asof")
    t = time.perf_counter()
    results += dq.run_all(con, contracts, bronze.read_ledger(lake), week)
    results += quarantine_checks(con, week)
    steps.steps.append({"step": "contract_checks", "seconds": round(time.perf_counter() - t, 3),
                        "rows_written": len(results)})
    record["quarantine_rows"] = con.execute("select count(*) from silver.quarantine").fetchone()[0]
    record["weeks_recomputed"] = len(weeks)
    con.close()
    errors = [r for r in results if not r["passed"] and r["severity"] == "error"]
    warns = [r for r in results if not r["passed"] and r["severity"] == "warn"]
    if errors:
        restored = _restore(lake, before)
        failures = [{"check": r["check_id"], "detail": f"observed {r['observed']}, expected {r['expected']}"}
                    for r in errors] + [{"check": "rollback", "detail": f"restored {len(restored)} table(s)"}]
        return _finish(lake, record, steps, results, "ROLLED_BACK", t0, failures)
    return _finish(lake, record, steps, results, "SUCCESS_WITH_WARNINGS" if warns else "SUCCESS", t0, [])


def _finish(lake: Lake, record: dict, steps: transform.Steps, results: list[dict], status: str, t0: float,
            failures: list[dict]) -> dict:
    record.update({
        "status": status, "finished_at": _now(), "duration_s": round(time.perf_counter() - t0, 3),
        "n_checks": len(results),
        "n_failed_error": sum(1 for r in results if not r["passed"] and r["severity"] == "error"),
        "n_failed_warn": sum(1 for r in results if not r["passed"] and r["severity"] == "warn"),
        "failures": json.dumps(failures), "steps": json.dumps(steps.steps),
        "table_versions": json.dumps(lake.versions())})
    _append(lake, "ops.run_log", [record], RUN_LOG_SCHEMA)
    _append(lake, "ops.dq_results", [{"run_id": record["run_id"], "week": record["week"], **r} for r in results],
            DQ_SCHEMA)
    out = lake.root / "_events" / "lineage"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{record['run_id']}.json").write_text(json.dumps(steps.events, indent=1), encoding="utf-8")
    return {**record, "failures": failures, "steps": steps.steps, "results": results, "events": steps.events}


def run_range(lake: Lake, landing: Path, first: int, last: int, contracts: dict | None = None,
              verbose: bool = False) -> list[dict]:
    out = []
    for week in range(first, last + 1):
        rec = run_week(lake, landing, week, contracts)
        out.append(rec)
        if verbose:
            print(f"{rec['run_id']:>18} {rec['status']:<22} {rec['duration_s']:>7.1f}s "
                  f"loaded={rec['bronze_rows_loaded']:>8} quarantine={rec['quarantine_rows']}", flush=True)
    return out


def versions_as_of(lake: Lake, week: int) -> dict[str, int]:
    """Table versions as they stood after the last successful run for `week` (for Delta time travel)."""
    runs = [r for r in read_run_log(lake) if r["week"] == week and r["status"].startswith("SUCCESS")]
    if not runs:
        raise KeyError(f"no successful run for week {week}")
    return json.loads(runs[-1]["table_versions"])
