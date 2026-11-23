"""Weekly replay, the bronze ingest gate and the run controller, on the miniature with a real Delta lake."""
import csv
import json
import shutil

import pytest

from oulad import bronze, pipeline
from oulad.common import FIRST_WEEK, LAST_WEEK, SOURCES, sha256_file
from oulad.lake import Lake
from oulad.replay import build_landing, write_manifest
from tests.helpers import CLICKS, source_rows, write_raw


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def landing(tmp_path_factory):
    base = tmp_path_factory.mktemp("replay")
    write_raw(base / "raw", bad=False)
    build_landing(base / "raw", base / "landing")
    return base / "landing"


def arrivals(landing, name):
    out = []
    for w in range(FIRST_WEEK, LAST_WEEK + 1):
        p = landing / f"week={w}" / f"{name}.csv"
        if p.exists():
            out += [(w, r) for r in read(p)]
    return out


def test_every_week_has_a_delivery_folder_and_a_manifest(landing):
    for w in range(FIRST_WEEK, LAST_WEEK + 1):
        m = json.loads((landing / f"week={w}" / "_manifest.json").read_text())
        assert m["week"] == w
        for name, info in m["files"].items():
            p = landing / f"week={w}" / f"{name}.csv"
            assert info["sha256"] == sha256_file(p) and info["rows"] == len(read(p))


def test_reference_tables_arrive_in_the_opening_batch(landing):
    for name in ("courses", "assessments", "vle"):
        assert {w for w, _ in arrivals(landing, name)} == {FIRST_WEEK}
        assert len(arrivals(landing, name)) == len(source_rows(False)[name])


def test_each_click_arrives_once_in_the_week_it_is_dated(landing):
    got = arrivals(landing, "studentVle")
    assert len(got) == len(CLICKS)
    for w, r in got:
        assert w == int(r["date"]) // 7                      # floor division: day -3 is week -1
    assert list(got[0][1]) == SOURCES["studentVle"][1]        # original column order
    assert {w for w, r in got if r["date"] == "-3"} == {-1}


def test_unregistration_date_is_hidden_until_it_happens(landing):
    reg = [(w, r) for w, r in arrivals(landing, "studentRegistration") if r["id_student"] == "2"]
    assert [(w, r["date_unregistration"]) for w, r in reg] == [(-1, "?"), (1, "12")]
    six = [(w, r["date_unregistration"]) for w, r in arrivals(landing, "studentRegistration") if r["id_student"] == "6"]
    assert six == [(-3, "?"), (4, "30")]
    # a student who never leaves is delivered once; an unknown registration date lands in the opening batch
    one = [(w, r["date_unregistration"]) for w, r in arrivals(landing, "studentRegistration")
           if r["id_student"] == "1" and r["code_presentation"] == "2013J"]
    assert one == [(-2, "?")]
    assert [w for w, r in arrivals(landing, "studentRegistration") if r["id_student"] == "4"] == [FIRST_WEEK]


def test_final_result_is_hidden_until_the_presentation_has_ended(landing):
    info = [(w, r["final_result"]) for w, r in arrivals(landing, "studentInfo")
            if r["id_student"] == "3" and r["code_presentation"] == "2013J"]
    assert info == [(0, "?"), (15, "Fail")]                  # 100 days -> last week 14 -> results in week 15
    assert all(r["final_result"] == "?" for w, r in arrivals(landing, "studentInfo") if w < 15)


def test_late_rows_change_only_the_arrival_week(tmp_path):
    rows = source_rows(False)
    rows["studentVle"] = [("AAA", "2013J", "1", "100", str(d), "1") for d in range(0, 60)] * 5
    write_raw(tmp_path / "raw", rows=rows)
    build_landing(tmp_path / "raw", tmp_path / "late", late_fraction=0.3)
    got = arrivals(tmp_path / "late", "studentVle")
    assert len(got) == 300
    delay = [w - int(r["date"]) // 7 for w, r in got]
    assert set(delay) == {0, 1, 2} and 40 < sum(1 for d in delay if d > 0) < 140


# --- ingest gate --------------------------------------------------------------------------------------

@pytest.fixture()
def area(landing, tmp_path):
    shutil.copytree(landing, tmp_path / "landing")
    lake = Lake(tmp_path / "lake")
    bronze.init_lake(lake)
    for w in (FIRST_WEEK, FIRST_WEEK + 1):
        bronze.ingest_week(lake, tmp_path / "landing", w, "setup")
    return lake, tmp_path / "landing", FIRST_WEEK + 2          # week -3: student 6 registers


def count(lake, table):
    return lake.table(table).to_pyarrow_table().num_rows


def failures(lake, landing, week):
    with pytest.raises(bronze.IngestError) as e:
        bronze.ingest_week(lake, landing, week, "t")
    return [f["check"] for f in e.value.failures]


def test_bronze_keeps_text_and_adds_load_metadata(area):
    lake, landing, week = area
    out = bronze.ingest_week(lake, landing, week, "run-1")
    assert out["loaded"] == {"studentInfo": 1, "studentRegistration": 1}
    t = lake.table("bronze.student_registration").to_pyarrow_table().to_pylist()
    row = [r for r in t if r["id_student"] == "6"][0]
    assert row["date_registration"] == "-20" and row["date_unregistration"] == "?"      # as landed, untyped
    assert (row["_batch_week"], row["_source_file"], row["_source_row"], row["_run_id"]) == \
        (week, "studentRegistration.csv", 1, "run-1")
    assert row["_file_sha256"] == sha256_file(landing / f"week={week}" / "studentRegistration.csv")
    assert row["_ingested_at"].endswith("+00:00")


def test_loading_the_same_week_twice_changes_nothing(area):
    lake, landing, week = area
    bronze.ingest_week(lake, landing, week, "a")
    before = (count(lake, "bronze.student_registration"), lake.version("bronze.student_registration"))
    out = bronze.ingest_week(lake, landing, week, "b")
    assert out["loaded"] == {} and sorted(out["skipped_identical"]) == ["studentInfo", "studentRegistration"]
    assert (count(lake, "bronze.student_registration"), lake.version("bronze.student_registration")) == before


def test_a_corrected_redelivery_replaces_the_earlier_one(area):
    lake, landing, week = area
    bronze.ingest_week(lake, landing, week, "a")
    n = count(lake, "bronze.student_registration")
    p = landing / f"week={week}" / "studentRegistration.csv"
    p.write_text(p.read_text().replace('"-20"', '"-21"'))
    write_manifest(landing / f"week={week}", week)
    out = bronze.ingest_week(lake, landing, week, "b")
    assert out["replaced"] == ["studentRegistration"] and out["loaded"] == {"studentRegistration": 1}
    rows = lake.table("bronze.student_registration").to_pyarrow_table().to_pylist()
    assert len(rows) == n and [r["date_registration"] for r in rows if r["id_student"] == "6"] == ["-21"]
    ledger = [r for r in bronze.read_ledger(lake) if r["batch_week"] == week and r["source"] == "studentRegistration"]
    assert len(ledger) == 1 and ledger[0]["file_sha256"] == sha256_file(p)


def test_gate_rejects_a_file_that_does_not_match_its_manifest(area):
    lake, landing, week = area
    p = landing / f"week={week}" / "studentRegistration.csv"
    p.write_bytes(p.read_bytes()[:-9])
    assert failures(lake, landing, week) == ["checksum_mismatch"]
    assert count(lake, "bronze.student_registration") == 1 and not any(r["batch_week"] == week for r in bronze.read_ledger(lake))


def test_gate_rejects_rows_with_the_wrong_number_of_fields(area):
    lake, landing, week = area
    p = landing / f"week={week}" / "studentRegistration.csv"
    p.write_text(p.read_text() + '"AAA","2013J"\n')
    write_manifest(landing / f"week={week}", week)
    assert failures(lake, landing, week) == ["unparseable_file"]


def test_gate_rejects_a_missing_column_and_warns_about_an_extra_one(area):
    lake, landing, week = area
    p = landing / f"week={week}" / "studentRegistration.csv"
    original = p.read_text()
    p.write_text(original.replace('"date_unregistration"', '"left_on"'))
    write_manifest(landing / f"week={week}", week)
    assert failures(lake, landing, week) == ["schema_drift"]
    lines = original.splitlines()
    p.write_text("\n".join([lines[0] + ',"note"'] + [x + ',"n"' for x in lines[1:]]) + "\n")
    write_manifest(landing / f"week={week}", week)
    out = bronze.ingest_week(lake, landing, week, "t")
    assert [w["check"] for w in out["warnings"]] == ["schema_drift_extra_column"]
    assert "note" not in lake.table("bronze.student_registration").to_pyarrow_table().column_names


def test_gate_rejects_a_file_already_loaded_for_another_week(area):
    lake, landing, week = area
    shutil.copy(landing / f"week={FIRST_WEEK}" / "studentRegistration.csv", landing / f"week={week}" / "studentRegistration.csv")
    write_manifest(landing / f"week={week}", week)
    assert failures(lake, landing, week) == ["duplicate_batch"]


def test_gate_rejects_a_gap_a_missing_manifest_and_unlisted_or_missing_files(area):
    lake, landing, week = area
    assert failures(lake, landing, week + 1) == ["missing_week"]
    d = landing / f"week={week}"
    (d / "studentVle.csv").write_text('"x"\n')
    assert failures(lake, landing, week) == ["unlisted_file"]
    (d / "studentVle.csv").unlink()
    (d / "studentInfo.csv").unlink()
    assert failures(lake, landing, week) == ["missing_file"]
    (d / "_manifest.json").unlink()
    assert failures(lake, landing, week) == ["missing_manifest"]
    shutil.rmtree(d)
    assert failures(lake, landing, week) == ["missing_delivery"]


def test_row_count_must_match_the_manifest(area):
    lake, landing, week = area
    d = landing / f"week={week}"
    m = json.loads((d / "_manifest.json").read_text())
    m["files"]["studentRegistration"]["rows"] += 1
    (d / "_manifest.json").write_text(json.dumps(m))
    assert failures(lake, landing, week) == ["row_count_mismatch"]


# --- run controller ------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runs(landing, tmp_path_factory):
    lake = Lake(tmp_path_factory.mktemp("runs") / "lake")
    return lake, pipeline.run_range(lake, landing, FIRST_WEEK, 2)


def test_runs_are_logged_with_status_steps_and_versions(runs):
    lake, recs = runs
    log = pipeline.read_run_log(lake)
    assert [r["week"] for r in log] == list(range(FIRST_WEEK, 3))
    assert all(r["status"] in ("SUCCESS", "SUCCESS_WITH_WARNINGS") and r["n_failed_error"] == 0 for r in log)
    assert log[0]["run_id"] == "week-05-attempt1" and log[-1]["as_of_day"] == 20
    steps = [s["step"] for s in json.loads(log[-1]["steps"])]
    assert steps[0] == "bronze.ingest" and steps[-1] == "contract_checks" and "silver.vle_daily" in steps
    assert json.loads(log[-1]["table_versions"])["gold.fact_student_week"] == lake.version("gold.fact_student_week")
    assert log[-1]["duration_s"] > 0 and log[-1]["n_checks"] == len(recs[-1]["results"]) > 100


def test_check_results_are_stored_for_every_run(runs):
    lake, recs = runs
    con = lake.connect()
    per_run = dict(con.execute("select run_id, count(*) from ops.dq_results group by 1").fetchall())
    assert per_run == {r["run_id"]: r["n_checks"] for r in recs}
    kinds = {r[0] for r in con.execute("select distinct kind from ops.dq_results").fetchall()}
    assert {"schema", "not_null", "range", "unique", "foreign_key", "custom", "reconciliation", "quarantine"} <= kinds


def test_term_time_snapshot_is_taken_in_the_week_before_the_cutoff(runs):
    lake, recs = runs
    assert "gold.features_asof" in [s["step"] for s in recs[-2]["steps"]]         # week 1 -> cut-off day 14
    assert "gold.features_asof" not in [s["step"] for s in recs[-1]["steps"]]
    con = lake.connect()
    assert con.execute("select distinct cutoff_day from gold.features_asof").fetchall() == [(14,)]
    # at that time nobody had a final result yet
    assert con.execute("select count(*), count(final_result) from gold.features_asof").fetchone() == (5, 0)


def test_time_travel_returns_the_tables_as_they_stood(runs):
    lake, _ = runs
    then = lake.connect(pipeline.versions_as_of(lake, 0))
    now = lake.connect()
    q = "select coalesce(sum(clicks), 0), max(week_index) from gold.fact_student_week"
    assert then.execute(q).fetchone() == (37, 0) and now.execute(q).fetchone() == (53 - 1, 2)
    with pytest.raises(KeyError):
        pipeline.versions_as_of(lake, 30)


def test_a_failed_check_rolls_back_and_blocks_later_weeks(landing, tmp_path):
    shutil.copytree(landing, tmp_path / "landing")
    lake = Lake(tmp_path / "lake")
    pipeline.run_range(lake, tmp_path / "landing", FIRST_WEEK, -1)
    before = {t: lake.table(t).to_pyarrow_table().num_rows for t in lake.tables() if t.startswith(("silver", "gold"))}
    p = tmp_path / "landing" / "week=0" / "studentVle.csv"
    good = p.read_text()
    p.write_text(good.replace('"1","101"', '"555","101"').replace('"7","200"', '"556","200"'))   # unknown students
    write_manifest(p.parent, 0)
    rec = pipeline.run_week(lake, tmp_path / "landing", 0)
    assert rec["status"] == "ROLLED_BACK"
    assert [f["check"] for f in rec["failures"]][0] == "quarantine_share_of_delivery"
    lake.forget()
    assert {t: lake.table(t).to_pyarrow_table().num_rows for t in before} == before
    assert "silver.quarantine" in before and before["silver.quarantine"] == 0
    assert lake.table("bronze.student_vle").to_pyarrow_table().num_rows == 2 + 6      # bad delivery kept as landed
    nxt = pipeline.run_week(lake, tmp_path / "landing", 1)
    assert nxt["status"] == "REJECTED_AT_INGEST" and nxt["failures"][0]["check"] == "unresolved_rollback"
    p.write_text(good)
    write_manifest(p.parent, 0)
    again = pipeline.run_week(lake, tmp_path / "landing", 0)
    assert again["status"].startswith("SUCCESS") and again["run_id"] == "week+00-attempt2"
    assert pipeline.run_week(lake, tmp_path / "landing", 1)["status"].startswith("SUCCESS")
    assert pipeline.unresolved_rollbacks(pipeline.read_run_log(lake), 2) == []


def test_unresolved_rollbacks_logic():
    log = [{"week": 1, "status": "ROLLED_BACK"}, {"week": 2, "status": "SUCCESS"}, {"week": 3, "status": "ROLLED_BACK"}]
    assert pipeline.unresolved_rollbacks(log, 3) == [1]
    assert pipeline.unresolved_rollbacks(log, 4) == [1, 3]
    assert pipeline.unresolved_rollbacks(log + [{"week": 1, "status": "SUCCESS"}], 4) == [3]
    assert pipeline.unresolved_rollbacks(log, 1) == []
