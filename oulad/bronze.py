"""Bronze: land each weekly delivery as received (all text), with load metadata, behind an ingest gate.

The gate rejects a whole delivery, and leaves the lake untouched, when
* the manifest is missing or names a file that is absent, or a file is present that the manifest omits;
* a file's SHA-256 or row count differs from the manifest (corrupt or truncated file);
* a file does not parse as CSV with the expected number of columns;
* a required column is missing (schema drift). An unexpected extra column is a warning and is dropped;
* a file has the same content hash as a different week's file already loaded (duplicated batch);
* an earlier week has not been loaded (missing week).

A delivery that passes is written with a partition overwrite on `_batch_week`, so loading the same week twice
leaves one copy (idempotent), and a corrected re-delivery replaces the earlier one.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import duckdb
import pyarrow as pa

from .common import FIRST_WEEK, META_COLUMNS, SOURCES, sha256_file
from .lake import Lake

LEDGER = "ops.batch_ledger"
LEDGER_SCHEMA = pa.schema([("source", pa.string()), ("batch_week", pa.int32()), ("file_sha256", pa.string()),
                           ("rows", pa.int64()), ("run_id", pa.string()), ("loaded_at", pa.string())])


class IngestError(Exception):
    def __init__(self, failures: list[dict]):
        super().__init__("; ".join(f"{f['check']}: {f['detail']}" for f in failures))
        self.failures = failures


def bronze_schema(source: str) -> pa.Schema:
    cols = [(c, pa.string()) for c in SOURCES[source][1]]
    meta = [("_batch_week", pa.int32()), ("_source_file", pa.string()), ("_file_sha256", pa.string()),
            ("_source_row", pa.int64()), ("_run_id", pa.string()), ("_ingested_at", pa.string())]
    assert [m[0] for m in meta] == META_COLUMNS
    return pa.schema(cols + meta)


def init_lake(lake: Lake) -> None:
    for source, (table, _) in SOURCES.items():
        if not lake.exists(f"bronze.{table}"):
            lake.write(f"bronze.{table}", bronze_schema(source).empty_table(), partition_by=["_batch_week"])
    if not lake.exists(LEDGER):
        lake.write(LEDGER, LEDGER_SCHEMA.empty_table())


def read_ledger(lake: Lake) -> list[dict]:
    return lake.table(LEDGER).to_pyarrow_table().to_pylist() if lake.exists(LEDGER) else []


def _header(path: Path) -> list[str]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        line = f.readline().rstrip("\r\n")
    return [c.strip().strip('"') for c in line.split(",")]


def inspect_delivery(week_dir: Path, week: int, ledger: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Return (failures, warnings, files) for one delivery without touching the lake."""
    failures, warnings, files = [], [], {}
    loaded_weeks = {r["batch_week"] for r in ledger}
    missing = [w for w in range(FIRST_WEEK, week) if w not in loaded_weeks]
    if missing:
        failures.append({"check": "missing_week", "detail": f"weeks not loaded before week {week}: {missing}"})
    if not week_dir.is_dir():
        failures.append({"check": "missing_delivery", "detail": f"no delivery folder for week {week}"})
        return failures, warnings, files
    manifest_path = week_dir / "_manifest.json"
    if not manifest_path.exists():
        failures.append({"check": "missing_manifest", "detail": "delivery has no _manifest.json"})
        return failures, warnings, files
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
    present = {p.stem: p for p in sorted(week_dir.glob("*.csv"))}
    for name in sorted(set(manifest) - set(present)):
        failures.append({"check": "missing_file", "detail": f"{name}.csv is in the manifest but not delivered"})
    for name in sorted(set(present) - set(manifest)):
        failures.append({"check": "unlisted_file", "detail": f"{name}.csv is not in the manifest"})
    by_hash = {(r["file_sha256"]): r for r in ledger}
    for name, path in present.items():
        if name not in SOURCES:
            failures.append({"check": "unknown_source", "detail": f"{name}.csv is not a known source table"})
            continue
        if name not in manifest:
            continue
        sha = sha256_file(path)
        if sha != manifest[name]["sha256"]:
            failures.append({"check": "checksum_mismatch", "detail": f"{name}.csv does not match its manifest hash"})
            continue
        header, expected = _header(path), SOURCES[name][1]
        lost = [c for c in expected if c not in header]
        extra = [c for c in header if c not in expected]
        if lost:
            failures.append({"check": "schema_drift", "detail": f"{name}.csv lacks column(s) {lost}; has {header}"})
            continue
        if extra:
            warnings.append({"check": "schema_drift_extra_column",
                             "detail": f"{name}.csv has unexpected column(s) {extra}; dropped"})
        dup = by_hash.get(sha)
        if dup and (dup["source"], dup["batch_week"]) != (name, week) and manifest[name]["rows"] > 0:
            failures.append({"check": "duplicate_batch",
                             "detail": f"{name}.csv is byte-identical to the week {dup['batch_week']} delivery"})
            continue
        files[name] = {"path": path, "sha256": sha, "header": header, "rows": manifest[name]["rows"]}
    return failures, warnings, files


def _read_file(con, name: str, info: dict, week: int, run_id: str, now: str) -> pa.Table:
    cols = ", ".join(f"'{c}': 'VARCHAR'" for c in info["header"])
    keep = ", ".join(SOURCES[name][1])
    rel = con.sql(f"""
        select {keep}, cast({week} as integer) as _batch_week, '{info['path'].name}' as _source_file,
               '{info['sha256']}' as _file_sha256, row_number() over () as _source_row,
               '{run_id}' as _run_id, '{now}' as _ingested_at
        from read_csv('{info['path'].as_posix()}', header = true, auto_detect = false, delim = ',', quote = '"',
                      escape = '"', columns = {{{cols}}}, strict_mode = true, encoding = 'utf-8')""")
    return rel.arrow().read_all().cast(bronze_schema(name))


def ingest_week(lake: Lake, landing: Path, week: int, run_id: str) -> dict:
    """Load one weekly delivery. Raises IngestError, with nothing written, if the gate fails."""
    ledger = read_ledger(lake)
    failures, warnings, files = inspect_delivery(Path(landing) / f"week={week}", week, ledger)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    con = duckdb.connect()
    tables = {}
    if not failures:
        for name, info in files.items():
            try:
                t = _read_file(con, name, info, week, run_id, now)
            except duckdb.Error as e:
                failures.append({"check": "unparseable_file",
                                 "detail": f"{name}.csv: {str(e).splitlines()[0][:160]}"})
                continue
            if t.num_rows != info["rows"]:
                failures.append({"check": "row_count_mismatch",
                                 "detail": f"{name}.csv has {t.num_rows} rows, manifest says {info['rows']}"})
            tables[name] = t
    con.close()
    if failures:
        raise IngestError(failures)
    known = {(r["source"], r["batch_week"]): r for r in ledger}
    loaded, skipped, replaced = {}, [], []
    for name, t in tables.items():
        prev = known.get((name, week))
        if prev and prev["file_sha256"] == files[name]["sha256"]:
            skipped.append(name)          # identical re-run: nothing to do
            continue
        if prev:
            replaced.append(name)         # corrected re-delivery replaces the earlier load
        lake.write(f"bronze.{SOURCES[name][0]}", t, partition_by=["_batch_week"], predicate=f"_batch_week = {week}")
        loaded[name] = t.num_rows
    # a source that was loaded for this week before but is absent from a corrected delivery is withdrawn
    for (name, w), prev in known.items():
        if w == week and name not in tables and name != "_empty":
            lake.write(f"bronze.{SOURCES[name][0]}", bronze_schema(name).empty_table(),
                       partition_by=["_batch_week"], predicate=f"_batch_week = {week}")
            replaced.append(name)
    rows = [r for r in ledger if r["batch_week"] != week]
    rows += [{"source": n, "batch_week": week, "file_sha256": files[n]["sha256"], "rows": t.num_rows,
              "run_id": run_id, "loaded_at": now} for n, t in tables.items()]
    if not tables:   # an empty week is still a loaded week
        rows.append({"source": "_empty", "batch_week": week, "file_sha256": "", "rows": 0, "run_id": run_id,
                     "loaded_at": now})
    lake.write(LEDGER, pa.Table.from_pylist(rows, schema=LEDGER_SCHEMA))
    return {"loaded": loaded, "skipped_identical": skipped, "replaced": replaced, "warnings": warnings,
            "rows": {n: t.num_rows for n, t in tables.items()}}
