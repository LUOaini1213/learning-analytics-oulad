"""Failure drills: break a weekly delivery on purpose and record whether the pipeline notices, whether the
published tables stay intact, and whether the documented remediation brings the lake back to the state of a
clean run. Each drill works on a private copy of a lake that has been loaded up to the week before.

`run_all` returns one record per drill; scripts/run_drills.py writes them to results/drills.json and
docs/runbook.md describes the remediation for each.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from . import pipeline
from .common import FIRST_WEEK
from .lake import Lake
from .replay import build_landing, write_manifest

DRILL_WEEK = 4          # the delivery that is tampered with
COMPARE = ["silver.vle_daily", "silver.student_registration", "silver.student_info", "silver.student_assessment",
           "silver.quarantine", "gold.fact_student_week", "gold.fact_engagement_weekly", "gold.fact_assessment",
           "gold.dim_student_presentation"]


def fingerprint(lake: Lake, versions: dict[str, int] | None = None, tables: list[str] | None = None) -> dict:
    """Row count and an order-independent content hash per table."""
    con = lake.connect(versions)
    out = {}
    for t in tables or COMPARE:
        if lake.exists(t):
            n, h = con.execute(f"select count(*), coalesce(bit_xor(hash(x)), 0) from {t} x").fetchone()
            out[t] = [n, str(h)]
    con.close()
    return out


def _copy(src: Path, dst: Path) -> Path:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def _rows(path: Path) -> list[list[str]]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def _write(path: Path, rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n").writerows(rows)


def _checks(rec: dict) -> list[str]:
    return sorted({r["check_id"] for r in rec["results"] if not r["passed"] and r["severity"] == "error"})


def _published(lake: Lake) -> dict:
    """Content of the published (silver and gold) tables; a rollback restores content, not version numbers."""
    return fingerprint(lake)


class Drills:
    def __init__(self, raw: Path, work: Path):
        self.raw, self.work = Path(raw), Path(work)
        if self.work.exists():
            shutil.rmtree(self.work)
        self.landing = self.work / "landing"
        build_landing(self.raw, self.landing)
        self.base = self.work / "base"                       # loaded up to the week before the drill
        pipeline.run_range(Lake(self.base), self.landing, FIRST_WEEK, DRILL_WEEK - 1)
        self.clean = Lake(_copy(self.base, self.work / "clean"))
        pipeline.run_range(self.clean, self.landing, DRILL_WEEK, DRILL_WEEK + 1)
        self.clean_after = fingerprint(self.clean)

    def _scenario(self, name: str) -> tuple[Lake, Path]:
        d = self.work / name
        return Lake(_copy(self.base, d / "lake")), _copy(self.landing, d / "landing")

    def _record(self, name, injected, expected, lake, landing, first, remediation, fix, before) -> dict:
        """`first` is the run that met the fault. Apply `fix`, re-run, and compare with the clean lake."""
        detected_by = _checks(first)
        protected = _published(lake) == before
        fix()
        reruns = pipeline.run_range(lake, landing, DRILL_WEEK, DRILL_WEEK + 1)
        return {"drill": name, "injected": injected, "expected_check": expected,
                "detected": any(expected in c for c in detected_by), "detected_by": detected_by,
                "status": first["status"], "published_tables_untouched": protected,
                "remediation": remediation, "status_after_remediation": [r["status"] for r in reruns],
                "matches_clean_run_after_remediation": fingerprint(lake) == self.clean_after}

    def _restore_week(self, landing: Path, week: int = DRILL_WEEK):
        def fix():
            _copy(self.landing / f"week={week}", landing / f"week={week}")
        return fix

    def _quiet_student(self, landing: Path) -> str:
        """A student active in the drill week whose rows are a small share of every delivery, so that holding
        them in quarantine stays below the share that rolls a run back."""
        totals: dict[int, int] = {}
        mine: dict[str, dict[int, int]] = {}
        for w in range(FIRST_WEEK, DRILL_WEEK + 1):
            for p in sorted((landing / f"week={w}").glob("*.csv")):
                rows = _rows(p)
                totals[w] = totals.get(w, 0) + len(rows) - 1
                if p.stem in ("studentVle", "studentAssessment"):
                    col = rows[0].index("id_student")
                    for r in rows[1:]:
                        mine.setdefault(r[col], {}).setdefault(w, 0)
                        mine[r[col]][w] += 1
        active = {s: c for s, c in mine.items() if c.get(DRILL_WEEK)}
        worst = {s: max(n / totals[w] for w, n in c.items()) for s, c in active.items()}
        return min(sorted(worst), key=lambda s: worst[s])

    # --- the drills -------------------------------------------------------------------------------------
    def corrupt_file(self) -> dict:
        lake, landing = self._scenario("corrupt_file")
        target = landing / f"week={DRILL_WEEK}" / "studentVle.csv"
        data = target.read_bytes()
        target.write_bytes(data[: len(data) // 2])            # truncated in transit; manifest still says the full file
        before = _published(lake)
        first = pipeline.run_week(lake, landing, DRILL_WEEK)
        return self._record("corrupt_file", "studentVle.csv cut off half-way (manifest unchanged)",
                            "checksum_mismatch", lake, landing, first,
                            "ask the source for the file again; re-run the week", self._restore_week(landing), before)

    def malformed_rows(self) -> dict:
        lake, landing = self._scenario("malformed_rows")
        d = landing / f"week={DRILL_WEEK}"
        text = (d / "studentVle.csv").read_text(encoding="utf-8").splitlines()
        text[5] = text[5] + ',"unexpected"'                   # one row has an extra field
        text[9] = text[9].rsplit(",", 2)[0]                   # one row has lost two fields
        (d / "studentVle.csv").write_text("\n".join(text) + "\n", encoding="utf-8")
        write_manifest(d, DRILL_WEEK)                         # the sender's manifest matches the broken file
        before = _published(lake)
        first = pipeline.run_week(lake, landing, DRILL_WEEK)
        return self._record("malformed_rows", "two rows with the wrong number of fields (manifest consistent)",
                            "unparseable_file", lake, landing, first,
                            "ask the source for a corrected file; re-run the week", self._restore_week(landing), before)

    def schema_drift(self) -> dict:
        lake, landing = self._scenario("schema_drift")
        d = landing / f"week={DRILL_WEEK}"
        rows = _rows(d / "studentVle.csv")
        rows[0] = ["clicks" if c == "sum_click" else c for c in rows[0]]   # the source renamed a column
        _write(d / "studentVle.csv", rows)
        write_manifest(d, DRILL_WEEK)
        before = _published(lake)
        first = pipeline.run_week(lake, landing, DRILL_WEEK)
        return self._record("schema_drift", "column sum_click renamed to clicks", "schema_drift", lake, landing,
                            first, "agree the change with the source, update the contract and the staging SQL, "
                            "or ask for the old layout; re-run the week", self._restore_week(landing), before)

    def duplicated_batch(self) -> dict:
        lake, landing = self._scenario("duplicated_batch")
        d = landing / f"week={DRILL_WEEK}"
        shutil.copy(landing / f"week={DRILL_WEEK - 1}" / "studentVle.csv", d / "studentVle.csv")
        write_manifest(d, DRILL_WEEK)                         # last week's clicks sent again as this week's
        before = _published(lake)
        first = pipeline.run_week(lake, landing, DRILL_WEEK)
        return self._record("duplicated_batch", "previous week's click file delivered again as this week's",
                            "duplicate_batch", lake, landing, first,
                            "ask the source for the right week; re-run the week", self._restore_week(landing), before)

    def missing_week(self) -> dict:
        lake, landing = self._scenario("missing_week")
        shutil.rmtree(landing / f"week={DRILL_WEEK}")
        before = _published(lake)
        first = pipeline.run_week(lake, landing, DRILL_WEEK + 1)   # the scheduler moves on to the next week
        return self._record("missing_week", "one weekly delivery never arrived; the next week is run",
                            "missing_week", lake, landing, first,
                            "obtain the missing delivery; run the weeks in order", self._restore_week(landing), before)

    def poisoned_batch(self) -> dict:
        lake, landing = self._scenario("poisoned_batch")
        d = landing / f"week={DRILL_WEEK}"
        rows = _rows(d / "studentVle.csv")
        col = rows[0].index("id_student")
        for r in rows[1:]:
            r[col] = "9" + r[col]                             # every student number is wrong, the file is well-formed
        _write(d / "studentVle.csv", rows)
        write_manifest(d, DRILL_WEEK)
        before = _published(lake)
        first = pipeline.run_week(lake, landing, DRILL_WEEK)
        rec = self._record("poisoned_batch", "well-formed click file whose student numbers match nobody",
                           "quarantine_share_of_delivery", lake, landing, first,
                           "inspect silver.quarantine in bronze as landed; ask the source for a corrected file; "
                           "re-run the week (the corrected file replaces the bad one)",
                           self._restore_week(landing), before)
        rec["bronze_kept_the_bad_delivery_until_replaced"] = first["bronze_rows_loaded"] > 0
        return rec

    def late_dimension(self) -> dict:
        """A registration arrives a week after the student's clicks: the clicks wait in quarantine."""
        lake, landing = self._scenario("late_dimension")
        student = self._quiet_student(landing)
        moved = []
        for w in range(FIRST_WEEK, DRILL_WEEK + 1):
            for name in ("studentRegistration", "studentInfo"):
                p = landing / f"week={w}" / f"{name}.csv"
                if p.exists():
                    rows = _rows(p)
                    col = rows[0].index("id_student")
                    keep = [r for i, r in enumerate(rows) if i == 0 or r[col] != student]
                    moved += [(name, rows[0], r) for r in rows[1:] if r[col] == student]
                    _write(p, keep)
            write_manifest(landing / f"week={w}", w)
        late = landing / f"week={DRILL_WEEK + 1}"
        for name in ("studentRegistration", "studentInfo"):
            p = late / f"{name}.csv"
            rows = _rows(p) if p.exists() else [next(h for n, h, _ in moved if n == name)]
            _write(p, rows + [r for n, _, r in moved if n == name])
        write_manifest(late, DRILL_WEEK + 1)
        fresh = Lake(self.work / "late_dimension" / "lake2")
        runs = pipeline.run_range(fresh, landing, FIRST_WEEK, DRILL_WEEK + 1)
        waiting = [r["quarantine_rows"] for r in runs]
        con = fresh.connect()
        reasons = dict(con.execute("select reject_reason, count(*) from silver.quarantine group by 1").fetchall())
        con.close()
        warned = any(r["check_id"] == "quarantine_is_empty" and not r["passed"] for r in runs[-2]["results"])
        same = fingerprint(fresh, tables=["silver.vle_daily", "gold.fact_student_week"]) == \
            {k: v for k, v in self.clean_after.items() if k in ("silver.vle_daily", "gold.fact_student_week")}
        return {"drill": "late_dimension", "injected": "one student's registration arrives after their clicks",
                "expected_check": "quarantine_is_empty", "detected": warned and max(waiting) > 0,
                "detected_by": ["quarantine_is_empty"] if warned else [],
                "status": runs[-2]["status"], "rows_quarantined_while_waiting": max(waiting),
                "rows_in_quarantine_after_registration_arrived": waiting[-1], "quarantine_reasons_at_end": reasons,
                "published_tables_untouched": None,
                "remediation": "none needed: the rows are released automatically when the registration arrives",
                "status_after_remediation": [runs[-1]["status"]],
                "matches_clean_run_after_remediation": same and waiting[-1] == 0}

    def late_data(self, late_fraction: float = 0.2) -> dict:
        """Click rows that arrive one or two weeks late (SYNTHETIC lateness)."""
        d = self.work / "late_data"
        landing = d / "landing"
        build_landing(self.raw, landing, late_fraction=late_fraction)
        lake = Lake(d / "lake")
        last = DRILL_WEEK + 3
        runs = pipeline.run_range(lake, landing, FIRST_WEEK, last)
        reference = Lake(_copy(self.clean.root, d / "reference"))
        pipeline.run_range(reference, self.landing, DRILL_WEEK + 2, last)
        con, ref = lake.connect(), reference.connect()
        late_rows = con.execute("select count(*) from silver.vle_daily where last_batch_week > event_week").fetchone()[0]
        q = (f"select count(*), coalesce(sum(clicks), 0), coalesce(bit_xor(hash(sp_key, week_index, clicks, active_days)), 0) "
             f"from gold.fact_student_week where week_index <= {last - 2}")
        settled_equal = con.execute(q).fetchone() == ref.execute(q).fetchone()
        # what the lecturers' snapshot for cut-off day 14 missed because rows were still on their way
        diff = con.execute("""
            select count(*) from gold.features_asof a
            join (select f.sp_key, sum(f.clicks) as clicks from gold.fact_student_week f where f.week_index < 2 group by 1) t
              on t.sp_key = a.sp_key
            where a.cutoff_day = 14 and a.clicks_total <> t.clicks""").fetchone()[0]
        n14 = con.execute("select count(*) from gold.features_asof where cutoff_day = 14").fetchone()[0]
        recon_ok = all(r["passed"] for run in runs for r in run["results"] if r["kind"] == "reconciliation")
        con.close(); ref.close()
        return {"drill": "late_data", "injected": f"{late_fraction:.0%} of click rows arrive one or two weeks late (SYNTHETIC)",
                "expected_check": "rows_arriving_late", "detected": late_rows > 0,
                "detected_by": ["silver.vle_daily.last_batch_week > event_week"], "status": runs[-1]["status"],
                "late_daily_rows_seen": late_rows, "reconciliations_passed_every_week": recon_ok,
                "published_tables_untouched": None,
                "remediation": "none needed: the weeks a late row belongs to are recomputed when it arrives",
                "status_after_remediation": [runs[-1]["status"]],
                "matches_clean_run_after_remediation": settled_equal,
                "term_time_snapshot_day14_students": n14,
                "term_time_snapshot_day14_students_with_undercounted_clicks": diff}

    def idempotent_rerun(self) -> dict:
        lake, landing = self._scenario("idempotent_rerun")
        pipeline.run_week(lake, landing, DRILL_WEEK)
        once = fingerprint(lake)
        again = pipeline.run_week(lake, landing, DRILL_WEEK)
        return {"drill": "idempotent_rerun", "injected": "the same weekly delivery is loaded twice",
                "expected_check": "none (must be harmless)", "detected": True, "detected_by": [],
                "status": again["status"], "bronze_rows_loaded_second_time": again["bronze_rows_loaded"],
                "published_tables_untouched": fingerprint(lake) == once, "remediation": "none needed",
                "status_after_remediation": [again["status"]],
                "matches_clean_run_after_remediation": fingerprint(lake) == once}

    def run_all(self) -> list[dict]:
        return [self.corrupt_file(), self.malformed_rows(), self.schema_drift(), self.duplicated_batch(),
                self.missing_week(), self.poisoned_batch(), self.late_dimension(), self.late_data(),
                self.idempotent_rerun()]


def summarise(records: list[dict]) -> dict:
    faults = [r for r in records if r["drill"] != "idempotent_rerun"]
    return {"n_drills": len(records), "n_fault_drills": len(faults),
            "n_detected": sum(1 for r in faults if r["detected"]),
            "n_recovered_to_clean_state": sum(1 for r in records if r["matches_clean_run_after_remediation"]),
            "data": "data/fixture (sample of the real extract)", "drill_week": DRILL_WEEK,
            "drills": records}


if __name__ == "__main__":  # pragma: no cover
    import sys
    print(json.dumps(summarise(Drills(Path(sys.argv[1]), Path(sys.argv[2])).run_all()), indent=1))
