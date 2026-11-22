"""Cut the published extract into weekly deliveries, as a source system would have sent them during term.

OULAD is published as one final extract. To make term-time analytics honest the extract is replayed week by
week on each presentation's own clock (day 0 = start of that presentation):

* reference tables (courses, assessments, vle) arrive in the opening batch;
* a click or a submission arrives in the batch of the week it is dated;
* a registration row arrives in the week of registration, with `date_unregistration` blank unless the student
  had already left; it is delivered again, with the date filled in, in the week the student leaves;
* a studentInfo row arrives with the registration, with `final_result` blank; the result arrives in the batch
  after the presentation's last week.

`late_fraction` > 0 holds back a deterministic share of click rows by one or two weeks. The published data has
no arrival times, so lateness is SYNTHETIC and is used only in the failure drills, never in the main results.
Each batch carries a manifest with row counts and SHA-256 hashes (control totals).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb

from .common import FIRST_WEEK, LAST_WEEK, SOURCES, sha256_file

WEEK = "cast(floor({e} / 7.0) as integer)"
NUM = "try_cast(nullif({c}, '?') as integer)"


def _wk(expr: str) -> str:
    return f"least(greatest({WEEK.format(e=expr)}, {FIRST_WEEK}), {LAST_WEEK})"


def _copy(con, select: str, out_dir: Path, name: str, columns: list[str]) -> None:
    """Write one CSV per arrival week, original column order, every field quoted as in the source."""
    con.execute(f"create or replace temp table _out as {select}")
    weeks = [r[0] for r in con.execute("select distinct arrival_week from _out order by 1").fetchall()]
    cols = ", ".join(columns)
    for w in weeks:
        d = out_dir / f"week={w}"
        d.mkdir(parents=True, exist_ok=True)
        target = (d / f"{name}.csv").as_posix()
        con.execute(f"copy (select {cols} from _out where arrival_week = {w} order by _rn) to '{target}' "
                    "(format csv, header true, force_quote *)")


def write_manifest(week_dir: Path, week: int) -> dict:
    files = {}
    for p in sorted(week_dir.glob("*.csv")):
        with open(p, "rb") as f:
            rows = sum(1 for _ in f) - 1
        files[p.stem] = {"rows": rows, "sha256": sha256_file(p)}
    (week_dir / "_manifest.json").write_text(json.dumps({"week": week, "files": files}, indent=1),
                                             encoding="utf-8")
    return files


def build_landing(raw_dir: Path, landing_dir: Path, late_fraction: float = 0.0) -> dict:
    raw_dir, landing_dir = Path(raw_dir), Path(landing_dir)
    if landing_dir.exists():
        shutil.rmtree(landing_dir)
    landing_dir.mkdir(parents=True)
    con = duckdb.connect()
    for name in SOURCES:
        path = (raw_dir / f"{name}.csv").as_posix()
        con.execute(f"create view src_{name} as select *, row_number() over () as _rn from "
                    f"read_csv('{path}', all_varchar = true, header = true)")
    reg_w = _wk(f"coalesce({NUM.format(c='r.date_registration')}, -9999)")
    unreg_w = _wk(NUM.format(c="r.date_unregistration"))
    for name in ("courses", "assessments", "vle"):
        _copy(con, f"select *, {FIRST_WEEK} as arrival_week from src_{name}", landing_dir, name, SOURCES[name][1])
    # registrations: first delivery in the registration week, second when the student leaves
    _copy(con, f"""
        select code_module, code_presentation, id_student, date_registration,
               case when {unreg_w} <= {reg_w} then date_unregistration else '?' end as date_unregistration,
               {reg_w} as arrival_week, _rn
        from src_studentRegistration r
        union all
        select code_module, code_presentation, id_student, date_registration, date_unregistration,
               {unreg_w} as arrival_week, _rn
        from src_studentRegistration r
        where {unreg_w} > {reg_w}""", landing_dir, "studentRegistration", SOURCES["studentRegistration"][1])
    info_cols = [c for c in SOURCES["studentInfo"][1] if c != "final_result"]
    info = ", ".join("i." + c for c in info_cols)
    result_w = f"least({WEEK.format(e='cast(c.module_presentation_length as integer)')} + 1, {LAST_WEEK})"
    _copy(con, f"""
        select {info}, '?' as final_result, {reg_w} as arrival_week, i._rn
        from src_studentInfo i join src_studentRegistration r using (code_module, code_presentation, id_student)
        union all
        select {info}, i.final_result, {result_w} as arrival_week, i._rn
        from src_studentInfo i join src_courses c using (code_module, code_presentation)""",
          landing_dir, "studentInfo", SOURCES["studentInfo"][1])
    _copy(con, f"select *, {_wk(NUM.format(c='date_submitted'))} as arrival_week from src_studentAssessment",
          landing_dir, "studentAssessment", SOURCES["studentAssessment"][1])
    late = ("0" if late_fraction <= 0 else
            f"case when (hash(_rn) % 100000) < {int(late_fraction * 100000)} "
            "then 1 + cast(hash(_rn, 7) % 2 as integer) else 0 end")
    _copy(con, f"select *, least({_wk(NUM.format(c='date'))} + {late}, {LAST_WEEK}) as arrival_week "
               "from src_studentVle", landing_dir, "studentVle", SOURCES["studentVle"][1])
    con.close()
    # a batch exists for every week, even when nothing arrived, so a gap is always a fault
    summary = {"weeks": [], "late_fraction": late_fraction}
    for w in range(FIRST_WEEK, LAST_WEEK + 1):
        d = landing_dir / f"week={w}"
        d.mkdir(exist_ok=True)
        files = write_manifest(d, w)
        summary["weeks"].append({"week": w, "files": {k: v["rows"] for k, v in files.items()}})
    return summary
