"""The whole weekly replay on the committed sample of real data, the failure drills, and lineage events."""
import json
import uuid
from pathlib import Path

import jsonschema
import pytest

from oulad import drills, lineage, pipeline
from oulad.common import FIRST_WEEK, LAST_WEEK

SCHEMA = json.loads((Path(__file__).parent / "schemas" / "OpenLineage-2-0-2.json").read_text(encoding="utf-8"))


def test_run_event_has_the_openlineage_shape():
    e = lineage.run_event("week+01-attempt1", "silver.x", ["bronze.a", "silver.x", "bronze.b"], "silver.x",
                          [("id", "int32"), ("name", "string")], 7, "2026-01-01T00:00:00.000+00:00")
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/RunEvent", "$defs": SCHEMA["$defs"]})
    assert list(validator.iter_errors(e)) == []
    assert [i["name"] for i in e["inputs"]] == ["bronze.a", "bronze.b"]         # the output is not its own input
    out = e["outputs"][0]
    assert out["name"] == "silver.x" and out["outputFacets"]["outputStatistics"]["rowCount"] == 7
    assert out["facets"]["schema"]["fields"] == [{"name": "id", "type": "int32"}, {"name": "name", "type": "string"}]
    assert uuid.UUID(e["run"]["runId"]) and e["eventType"] == "COMPLETE"
    assert e["run"]["runId"] != lineage.run_event("week+02-attempt1", "silver.x", [], "silver.x", [], 0)["run"]["runId"]
    broken = dict(e, eventType="DONE")
    assert list(validator.iter_errors(broken))                                  # the validator can fail
    del broken["eventType"], broken["run"]
    assert list(validator.iter_errors(broken))


def test_mermaid_and_edges():
    ev = [lineage.run_event("r", "silver.b", ["bronze.a"], "silver.b", [], 1),
          lineage.run_event("r", "gold.c", ["silver.b", "bronze.a"], "gold.c", [], 1)]
    assert lineage.edges(ev) == [("bronze.a", "silver.b"), ("silver.b", "gold.c"), ("bronze.a", "gold.c")]
    text = lineage.mermaid(ev)
    assert text.startswith("flowchart LR") and "bronze_a --> silver_b" in text and "subgraph gold" in text
    assert text.index("subgraph bronze") < text.index("subgraph silver") < text.index("subgraph gold")


@pytest.mark.slow
def test_every_week_loads_without_an_error_level_failure(fx):
    lake, _, runs, s = fx
    assert [r["week"] for r in runs] == list(range(FIRST_WEEK, LAST_WEEK + 1))
    assert all(r["status"] in ("SUCCESS", "SUCCESS_WITH_WARNINGS") for r in runs)
    assert s["checks_all_runs"]["failed_error"] == 0 and s["runs"]["n"] == 45
    assert s["layers"]["bronze.student_vle"] == s["source_rows"]["studentVle"] == 39280
    assert s["layers"]["gold.dim_student_presentation"] == 160 and s["students"]["module_presentations"] == 4


@pytest.mark.slow
def test_layers_reconcile_at_the_end(fx):
    _, _, runs, s = fx
    assert len(s["reconciliation"]) == 9 and all(r["passed"] and r["left"] == r["right"] for r in s["reconciliation"])
    assert s["clicks"]["source_rows"] - s["clicks"]["rows_folded_into_daily_totals"] == s["clicks"]["silver_rows"]
    assert s["clicks"]["rows_folded_into_daily_totals"] > 0 and s["clicks"]["rows_arriving_late"] == 0
    for r in runs:      # and in every single week, not only the last
        assert all(c["passed"] for c in r["results"] if c["kind"] == "reconciliation"), r["run_id"]


@pytest.mark.slow
def test_term_time_snapshots_equal_the_features_rebuilt_with_hindsight(fx):
    _, _, _, s = fx
    eq = s["asof_equivalence"]
    assert eq["identical"] and set(eq["cutoffs"]) == {"14", "28", "56", "84"}
    for v in eq["cutoffs"].values():
        assert v["rows_snapshot"] == v["rows_rebuilt"] == v["rows_matched"] > 100
    assert eq["feature_columns_compared"] >= 30


@pytest.mark.slow
def test_snapshot_was_taken_before_later_data_existed(fx):
    lake, _, _, _ = fx
    then = lake.connect(pipeline.versions_as_of(lake, 1))
    assert then.execute("select max(week_index) from gold.fact_student_week").fetchone()[0] == 1
    assert then.execute("select count(final_result) from gold.dim_student_presentation").fetchone()[0] == 0
    assert then.execute("select count(*) from gold.dim_student_presentation where date_unregistration > 13").fetchone()[0] == 0
    now = lake.connect()
    assert now.execute("select count(*) from gold.dim_student_presentation where date_unregistration > 13").fetchone()[0] > 0


@pytest.mark.slow
def test_real_sample_features_do_not_leak(fx):
    from oulad import leakage, transform
    lake, _, _, _ = fx
    con = lake.connect()
    for c in pipeline.CUTOFFS:
        assert leakage.leaking_columns(con, transform.features_sql(), c) == []


@pytest.mark.slow
def test_lineage_events_cover_every_table_and_validate(fx):
    lake, _, runs, _ = fx
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/RunEvent", "$defs": SCHEMA["$defs"]})
    events = json.loads((lake.root / "_events" / "lineage" / f"{runs[-1]['run_id']}.json").read_text())
    events += json.loads((lake.root / "_events" / "lineage" / "finalise.json").read_text())
    for e in events:
        assert list(validator.iter_errors(e)) == []
    outputs = {e["outputs"][0]["name"] for e in events}
    assert {t for t in lake.tables() if t.split(".")[0] in ("silver", "gold")} - {"gold.features_asof"} <= outputs
    es = lineage.edges(events)
    assert ("bronze.student_vle", "silver.vle_daily") in es and ("silver.vle_daily", "gold.fact_student_week") in es
    assert ("gold.fact_student_week", "gold.features_early_warning") in es
    assert ("landing.studentInfo", "bronze.student_info") in es


@pytest.mark.slow
def test_every_fault_drill_is_detected_and_recovered(drill_records):
    by = {r["drill"]: r for r in drill_records}
    expected = {"corrupt_file": "REJECTED_AT_INGEST", "malformed_rows": "REJECTED_AT_INGEST",
                "schema_drift": "REJECTED_AT_INGEST", "duplicated_batch": "REJECTED_AT_INGEST",
                "missing_week": "REJECTED_AT_INGEST", "poisoned_batch": "ROLLED_BACK"}
    for name, status in expected.items():
        r = by[name]
        assert r["detected"] and r["status"] == status, name
        assert any(r["expected_check"] in c for c in r["detected_by"]), name
        assert r["published_tables_untouched"] is True, name
        assert all(s.startswith("SUCCESS") for s in r["status_after_remediation"]), name
        assert r["matches_clean_run_after_remediation"] is True, name
    assert by["poisoned_batch"]["bronze_kept_the_bad_delivery_until_replaced"]
    s = drills.summarise(drill_records)
    assert (s["n_drills"], s["n_fault_drills"], s["n_detected"], s["n_recovered_to_clean_state"]) == (9, 8, 8, 9)


@pytest.mark.slow
def test_late_rows_and_late_registrations_settle_without_intervention(drill_records):
    by = {r["drill"]: r for r in drill_records}
    d = by["late_dimension"]
    assert d["detected"] and d["rows_quarantined_while_waiting"] > 0
    assert d["rows_in_quarantine_after_registration_arrived"] == 0 and d["matches_clean_run_after_remediation"]
    late = by["late_data"]
    assert late["late_daily_rows_seen"] > 100 and late["reconciliations_passed_every_week"]
    assert late["matches_clean_run_after_remediation"]
    assert 0 < late["term_time_snapshot_day14_students_with_undercounted_clicks"] <= late["term_time_snapshot_day14_students"]
    idem = by["idempotent_rerun"]
    assert idem["bronze_rows_loaded_second_time"] == 0 and idem["published_tables_untouched"]


@pytest.mark.slow
def test_fingerprint_tells_two_states_apart(fx):
    lake, _, _, _ = fx
    now = drills.fingerprint(lake)
    earlier = drills.fingerprint(lake, pipeline.versions_as_of(lake, 3))
    assert now == drills.fingerprint(lake) and now != earlier
    assert now["gold.fact_student_week"][0] > earlier["gold.fact_student_week"][0] > 0
