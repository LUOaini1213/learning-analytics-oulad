"""Turn the YAML data contracts into SQL checks and run them.

Each check returns one result row: (check_id, table, kind, severity, passed, observed, expected, detail).
Kinds: schema, not_null, range, allowed, regex, unique, foreign_key, custom, reconciliation, volume.
"""
from __future__ import annotations

import statistics
from pathlib import Path

import duckdb
import yaml

from .common import CONTRACTS


def load_contracts(path: Path | None = None) -> dict:
    return yaml.safe_load((path or CONTRACTS / "contracts.yml").read_text(encoding="utf-8"))


def _scalar(con, sql: str):
    """One value; a query that cannot run (for example a table has gone) counts as a failed check."""
    try:
        return con.execute(sql).fetchone()[0]
    except duckdb.Error as e:
        return f"query failed: {str(e).splitlines()[0][:120]}"


def _lit(v) -> str:
    return str(v) if isinstance(v, (int, float)) else "'" + str(v).replace("'", "''") + "'"


def _result(check_id, table, kind, severity, passed, observed, expected, detail=""):
    return {"check_id": check_id, "table": table, "kind": kind, "severity": severity, "passed": bool(passed),
            "observed": str(observed), "expected": str(expected), "detail": detail}


def table_checks(con, table: str, spec: dict) -> list[dict]:
    out = []
    schema, name = table.split(".")
    actual = dict(con.execute("select column_name, data_type from information_schema.columns "
                              f"where table_schema = '{schema}' and table_name = '{name}'").fetchall())
    if not actual:
        return [_result(f"{table}:exists", table, "schema", "error", False, "missing", "table exists")]
    for col, rule in (spec.get("columns") or {}).items():
        cid = f"{table}:{col}"
        if col not in actual:
            out.append(_result(f"{cid}:type", table, "schema", "error", False, "column missing", rule["type"]))
            continue
        out.append(_result(f"{cid}:type", table, "schema", "error", actual[col] == rule["type"], actual[col],
                           rule["type"]))
        if not rule.get("nullable", True):
            n = _scalar(con, f"select count(*) from {table} where {col} is null")
            out.append(_result(f"{cid}:not_null", table, "not_null", "error", n == 0, n, 0))
        if "min" in rule or "max" in rule:
            conds = ([f"{col} < {rule['min']}"] if "min" in rule else []) + \
                    ([f"{col} > {rule['max']}"] if "max" in rule else [])
            n = _scalar(con, f"select count(*) from {table} where {' or '.join(conds)}")
            out.append(_result(f"{cid}:range", table, "range", "error", n == 0, n, 0,
                               f"allowed {rule.get('min', '-inf')}..{rule.get('max', 'inf')}"))
        if "allowed" in rule:
            vals = ", ".join(_lit(v) for v in rule["allowed"])
            n = _scalar(con, f"select count(*) from {table} where {col} is not null and {col} not in ({vals})")
            out.append(_result(f"{cid}:allowed", table, "allowed", "error", n == 0, n, 0))
        if "regex" in rule:
            n = _scalar(con, f"select count(*) from {table} where {col} is not null and "
                             f"not regexp_matches({col}, {_lit(rule['regex'])})")
            out.append(_result(f"{cid}:regex", table, "regex", "error", n == 0, n, 0))
    pk = spec.get("primary_key")
    if pk:
        cols = ", ".join(pk)
        n = _scalar(con, f"select count(*) from (select {cols} from {table} group by {cols} having count(*) > 1) d")
        out.append(_result(f"{table}:unique", table, "unique", "error", n == 0, n, 0, f"key ({cols})"))
    for fk in spec.get("foreign_keys") or []:
        cond = " and ".join(f"p.{c} = c.{c}" for c in fk["columns"])
        n = _scalar(con, f"select count(*) from {table} c where not exists "
                         f"(select 1 from {fk['references']} p where {cond})")
        out.append(_result(f"{table}:fk:{fk['references']}", table, "foreign_key", "error", n == 0, n, 0,
                           f"({', '.join(fk['columns'])}) -> {fk['references']}"))
    for chk in spec.get("checks") or []:
        n = _scalar(con, chk["sql"])
        expect = chk.get("expect", 0)
        out.append(_result(f"{table}:{chk['id']}", table, "custom", chk.get("severity", "error"), n == expect, n,
                           expect, chk.get("description", "")))
    return out


def reconciliation_checks(con, recs: list[dict]) -> list[dict]:
    out = []
    for r in recs:
        left, right = _scalar(con, r["left"]), _scalar(con, r["right"])
        out.append(_result(f"reconcile:{r['id']}", "", "reconciliation", r.get("severity", "error"),
                           left == right, left, right, r.get("description", "")))
    return out


def volume_ratio(current: float, history: list[float]) -> float | None:
    """Rows in this delivery divided by the median of the earlier deliveries in the window."""
    history = [h for h in history if h is not None]
    if not history:
        return None
    med = statistics.median(history)
    return None if med == 0 else current / med


def volume_check(ledger: list[dict], week: int, rule: dict) -> list[dict]:
    if not rule or not (rule["applies_from_week"] <= week <= rule["applies_to_week"]):
        return []
    rows = {r["batch_week"]: r["rows"] for r in ledger if r["source"] == rule["source"]}
    history = [rows.get(w, 0) for w in range(week - rule["window_weeks"], week)]
    ratio = volume_ratio(rows.get(week, 0), history)
    ok = ratio is not None and rule["min_ratio"] <= ratio <= rule["max_ratio"]
    return [_result(f"volume:{rule['source']}", f"bronze.{rule['source']}", "volume", rule.get("severity", "warn"),
                    ok, "n/a" if ratio is None else f"{ratio:.3f}", f"{rule['min_ratio']}..{rule['max_ratio']}",
                    f"rows this week / median of previous {rule['window_weeks']} weeks")]


def run_all(con, contracts: dict, ledger: list[dict], week: int) -> list[dict]:
    out = []
    for table, spec in contracts["tables"].items():
        out += table_checks(con, table, spec)
    out += reconciliation_checks(con, contracts.get("reconciliations") or [])
    out += volume_check(ledger, week, contracts.get("volume"))
    return out
