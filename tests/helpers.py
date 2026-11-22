"""A hand-written miniature of the extract, small enough to work every expected value out on paper.

Module AAA, presentation 2013J (100 days):
  assessments  11 TMA due day 10 (weight 40) | 12 CMA due day 20 (weight 60) | 13 Exam, no date (weight 100)
  materials    100 resource | 101 forumng | 102 quiz | 103 homepage
  students     1 registered day -10, passes
               2 registered day -5, unregisters day 12 (Withdrawn)
               3 registered day 3, fails
               4 registration date unknown, passes
               5 registered day 20, passes
               6 registered day -20, unregisters day 30 (Withdrawn); has a banked result for assessment 11
Module AAA, presentation 2014J: student 1 again (fails) and student 7 (Distinction); assessment 21, material 200.
"""
from __future__ import annotations

import csv
from pathlib import Path

import duckdb
import pyarrow as pa

from oulad import transform
from oulad.common import SOURCES, week_of
from oulad.lake import MemLake

COURSES = [("AAA", "2013J", "100"), ("AAA", "2014J", "100")]
ASSESSMENTS = [("AAA", "2013J", "11", "TMA", "10", "40"), ("AAA", "2013J", "12", "CMA", "20", "60"),
               ("AAA", "2013J", "13", "Exam", "?", "100"), ("AAA", "2014J", "21", "TMA", "10", "100")]
VLE = [("100", "AAA", "2013J", "resource", "?", "?"), ("101", "AAA", "2013J", "forumng", "?", "?"),
       ("102", "AAA", "2013J", "quiz", "1", "2"), ("103", "AAA", "2013J", "homepage", "?", "?"),
       ("200", "AAA", "2014J", "resource", "?", "?")]
# module, presentation, student, date_registration, date_unregistration
REGISTRATION = [("AAA", "2013J", "1", "-10", "?"), ("AAA", "2013J", "2", "-5", "12"), ("AAA", "2013J", "3", "3", "?"),
                ("AAA", "2013J", "4", "?", "?"), ("AAA", "2013J", "5", "20", "?"), ("AAA", "2013J", "6", "-20", "30"),
                ("AAA", "2014J", "1", "-3", "?"), ("AAA", "2014J", "7", "-1", "?")]
RESULT = {("2013J", "1"): "Pass", ("2013J", "2"): "Withdrawn", ("2013J", "3"): "Fail", ("2013J", "4"): "Pass",
          ("2013J", "5"): "Pass", ("2013J", "6"): "Withdrawn", ("2014J", "1"): "Fail", ("2014J", "7"): "Distinction"}
DEMO = {"1": ("M", "Scotland", "A Level or Equivalent", "10-20", "0-35", "0", "60", "N"),
        "2": ("F", "Wales", "Lower Than A Level", "0-10%", "35-55", "1", "120", "Y"),
        "3": ("F", "Scotland", "HE Qualification", "?", "0-35", "0", "60", "N"),
        "4": ("M", "Wales", "A Level or Equivalent", "90-100%", "55<=", "0", "30", "N"),
        "5": ("M", "Scotland", "No Formal quals", "50-60%", "0-35", "2", "60", "N"),
        "6": ("F", "Wales", "A Level or Equivalent", "20-30%", "35-55", "0", "90", "Y"),
        "7": ("M", "Ireland", "Post Graduate Qualification", "80-90%", "0-35", "0", "60", "N")}
# presentation, student, site, date, clicks
CLICKS = [("2013J", "1", "100", "-3", "2"), ("2013J", "1", "100", "-3", "3"), ("2013J", "1", "101", "0", "4"),
          ("2013J", "1", "102", "8", "1"), ("2013J", "1", "103", "13", "6"), ("2013J", "1", "100", "14", "7"),
          ("2013J", "1", "100", "27", "1"),
          ("2013J", "2", "100", "1", "10"), ("2013J", "2", "100", "13", "1"),
          ("2013J", "3", "101", "5", "2"), ("2013J", "3", "101", "6", "2"),
          ("2014J", "1", "200", "2", "9"), ("2014J", "7", "200", "3", "5")]
BAD_CLICKS = [("2013J", "1", "999", "2", "1"),    # orphan_site
              ("2013J", "99", "100", "2", "1"),   # orphan_student
              ("2013J", "1", "100", "x", "1"),    # bad_date
              ("2013J", "1", "100", "3", "0"),    # click_count_out_of_range
              ("2013J", "1", "100", "900", "1")]  # date_out_of_range
# assessment, student, date_submitted, is_banked, score
SUBMISSIONS = [("11", "1", "9", "0", "80"), ("12", "1", "22", "0", "60"), ("11", "3", "12", "0", "40"),
               ("11", "6", "-1", "1", "70"), ("13", "1", "95", "0", "55"), ("11", "2", "11", "0", "?"),
               ("21", "1", "9", "0", "30"), ("21", "7", "8", "0", "90")]
BAD_SUBMISSIONS = [("99", "1", "5", "0", "50"),    # orphan_assessment
                   ("11", "99", "5", "0", "50"),   # orphan_student
                   ("11", "5", "5", "0", "101")]   # score_out_of_range


def source_rows(bad: bool = True) -> dict[str, list[tuple]]:
    """The miniature as it would appear in the published CSV files (final extract)."""
    info = [("AAA", p, s) + DEMO[s] + (RESULT[(p, s)],) for _, p, s, _, _ in REGISTRATION]
    clicks = [("AAA", p, s, site, d, n) for p, s, site, d, n in CLICKS + (BAD_CLICKS if bad else [])]
    return {"courses": COURSES, "assessments": ASSESSMENTS, "vle": VLE, "studentInfo": info,
            "studentRegistration": REGISTRATION, "studentAssessment": SUBMISSIONS + (BAD_SUBMISSIONS if bad else []),
            "studentVle": clicks}


def write_raw(folder: Path, bad: bool = False, rows: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in (rows or source_rows(bad)).items():
        with open(folder / f"{name}.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
            w.writerow(SOURCES[name][1])
            w.writerows(data)
    return folder


def _batch(name: str, row: tuple) -> int:
    cols = SOURCES[name][1]
    val = dict(zip(cols, row))
    if name == "studentVle":
        return week_of(int(val["date"])) if val["date"].lstrip("-").isdigit() else 0
    if name == "studentAssessment":
        return week_of(int(val["date_submitted"]))
    return -5


def mem_lake(bad: bool = True, rows: dict | None = None, extra: dict | None = None) -> MemLake:
    """Bronze tables in memory, loaded as if every row had arrived in the week it is dated."""
    lake = MemLake()
    rows = rows or source_rows(bad)
    for name, (table, cols) in SOURCES.items():
        data = list(rows.get(name, [])) + list((extra or {}).get(name, []))
        records = []
        for i, r in enumerate(data, start=1):
            rec = dict(zip(cols, r))
            batch = r[len(cols)] if len(r) > len(cols) else _batch(name, r)
            rec.update({"_batch_week": batch, "_source_file": f"{name}.csv", "_file_sha256": "x",
                        "_source_row": i, "_run_id": "test", "_ingested_at": "t"})
            records.append(rec)
        schema = pa.schema([(c, pa.string()) for c in cols] + [("_batch_week", pa.int32()), ("_source_file", pa.string()),
                           ("_file_sha256", pa.string()), ("_source_row", pa.int64()), ("_run_id", pa.string()),
                           ("_ingested_at", pa.string())])
        lake.write(f"bronze.{table}", pa.Table.from_pylist(records, schema=schema))
    return lake


def build(lake: MemLake, cutoffs: list[int] | None = None) -> duckdb.DuckDBPyConnection:
    """Run the real silver and gold steps (and optionally the features) on a MemLake."""
    con = lake.con
    transform.prepare(con)
    steps = transform.Steps("test")
    weeks = transform.run_silver(lake, con, steps, [], full_refresh=True)
    transform.run_gold(lake, con, steps, weeks)
    if cutoffs:
        transform.build_features(lake, con, steps, cutoffs, "gold.features_early_warning")
    lake.steps = steps
    return con


def sp_key(con, presentation: str, student: int) -> str:
    return con.execute("select substr(sha256(concat((select pseudonym_salt from run.settings), '|', 'AAA', '|', ?, '|', "
                       "cast(? as varchar))), 1, 12)", [presentation, student]).fetchone()[0]


def rows(con, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()
