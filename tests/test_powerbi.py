"""The Power BI project as text: every DAX measure, read from the TMDL files, against an independent pandas
computation from the CSV extracts in many filter contexts; the security roles; and the generated files."""
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pbitest.dax import DaxError, Evaluator
from pbitest.model import DATA, DEFINITION, LoadedModel
from scripts import build_powerbi

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def model():
    return LoadedModel()


@pytest.fixture(scope="module")
def ev(model):
    return Evaluator(model)


# --- independent reference -----------------------------------------------------------------------------------

def visible(frames, table, f):
    """Rows of `table` left by the filters {"Table.Column": {values}}: its own columns, and the dimension
    tables it points to through MpKey and WeekIndex."""
    df = frames[table]
    for key, vals in f.items():
        t, c = key.split(".")
        if t == table:
            df = df[df[c].isin(list(vals))]
    for dim, key in (("DimModulePresentation", "MpKey"), ("DimWeek", "WeekIndex")):
        if table != dim and key in df.columns and table != "SecLecturerModule":
            d = frames[dim]
            hit = False
            for k, vals in f.items():
                t, c = k.split(".")
                if t == dim:
                    d, hit = d[d[c].isin(list(vals))], True
            if hit:
                df = df[df[key].isin(set(d[key]))]
    return df


def total(s):
    s = s.dropna()
    return None if s.empty else float(s.sum())


def ratio(a, b):
    return None if a is None or b is None or b == 0 else a / b


def count(df):
    return None if len(df) == 0 else len(df)


def without(f, key):
    """CALCULATE with a filter on a column replaces any slicer on that same column."""
    return {k: v for k, v in f.items() if k != key}


def reference(frames, f):
    ss = visible(frames, "StudentSnapshot", f)
    ss_flag = visible(frames, "StudentSnapshot", without(f, "StudentSnapshot.Flagged"))
    rl_status = visible(frames, "OpsRunLog", without(f, "OpsRunLog.Status"))
    og_supp = visible(frames, "AggOutcomeByGroup", without(f, "AggOutcomeByGroup.Suppressed"))
    mw, aw = visible(frames, "AggModuleWeek", f), visible(frames, "AggActivityWeek", f)
    oc, og = visible(frames, "AggOutcome", f), visible(frames, "AggOutcomeByGroup", f)
    wd, rl, ck = visible(frames, "AggWithdrawal", f), visible(frames, "OpsRunLog", f), visible(frames, "OpsChecks", f)
    aw_all_groups = visible(frames, "AggActivityWeek", {k: v for k, v in f.items() if k != "AggActivityWeek.ActivityGroup"})
    gone = ratio(total(wd["LeftByWeekEnd"]), total(wd["Followed"]))
    w, fl, reg = total(oc["Withdrawn"]), total(oc["Failed"]), total(oc["Students"])
    run, failed = total(ck["Checks"]), total(ck["Failed"])
    return {
        "Students": count(ss), "Flagged Students": count(ss_flag[ss_flag["Flagged"] == 1]),
        "Flagged Share": ratio(count(ss_flag[ss_flag["Flagged"] == 1]), count(ss)),
        "Inactive Students": count(ss[ss["ClicksLast2w"] == 0]),
        "Inactive Share": ratio(count(ss[ss["ClicksLast2w"] == 0]), count(ss)),
        "Students Missing an Assessment": count(ss[ss["AssessmentsMissed"] > 0]),
        "Average Score": None if ss["AvgScore"].dropna().empty else float(ss["AvgScore"].dropna().mean()),
        "Average Risk Score": None if ss.empty else float(ss["RiskScore"].mean()),
        "Median Days Since Last Active": None if ss.empty else float(np.median(ss["DaysSinceLastActive"].astype(float))),
        "Enrolled Students": total(mw["EnrolledStudents"]), "Active Students": total(mw["ActiveStudents"]), "Clicks": total(mw["Clicks"]),
        "Active Share": ratio(total(mw["ActiveStudents"]), total(mw["EnrolledStudents"])),
        "Clicks per Active Student": ratio(total(mw["Clicks"]), total(mw["ActiveStudents"])),
        "Activity Clicks": total(aw["Clicks"]), "Activity Share": ratio(total(aw["Clicks"]), total(aw_all_groups["Clicks"])),
        "Registrations": reg, "Withdrawn": w, "Failed": fl, "Passed": total(oc["Passed"]),
        "Withdrawal Rate": ratio(w, reg),
        "At-risk Rate": ratio(None if w is None and fl is None else (w or 0) + (fl or 0), reg),
        "Pass Rate": ratio(total(oc["Passed"]), reg),
        "Group Students": total(og["Students"]), "Group At-risk Rate": ratio(total(og["AtRisk"]), total(og["StudentsWithRate"])),
        "Cells Withheld": count(og_supp[og_supp["Suppressed"] == 1]),
        "Still Registered Share": None if gone is None else 1 - gone,
        "Runs": count(rl), "Runs Stopped": count(rl_status[rl_status["Status"].isin(["REJECTED_AT_INGEST", "ROLLED_BACK"])]),
        "Rows Loaded": total(rl["RowsLoaded"]), "Run Seconds": total(rl["DurationSeconds"]),
        "Peak Quarantine Rows": None if rl.empty else float(rl["QuarantineRows"].max()),
        "Checks Run": run, "Checks Failed": failed,
        "Check Pass Rate": ratio(None if run is None else run - (failed or 0), run),
    }


CONTEXTS = (
    [{}]
    + [{"DimModulePresentation.Module": {m}} for m in ("AAA", "BBB", "CCC", "GGG")]
    + [{"DimModulePresentation.MpKey": {k}} for k in ("AAA-2013J", "BBB-2014J", "CCC-2014J", "FFF-2013B", "GGG-2014B")]
    + [{"DimModulePresentation.Year": {2013}}, {"DimModulePresentation.Year": {2014}, "DimModulePresentation.StartMonth": {"February"}}]
    + [{"DimModulePresentation.IsCurrentTerm": {"Yes"}}, {"DimModulePresentation.MpKey": {"ZZZ-2099J"}}]
    + [{"StudentSnapshot.CutoffDay": {c}} for c in (14, 28, 56, 84)]
    + [{"StudentSnapshot.CutoffDay": {28}, "DimModulePresentation.MpKey": {k}} for k in ("AAA-2014J", "BBB-2014J", "DDD-2014J", "BBB-2013J")]
    + [{"StudentSnapshot.Flagged": {1}}, {"StudentSnapshot.Flagged": {0}, "StudentSnapshot.CutoffDay": {56}}]
    + [{"DimWeek.WeekIndex": {w}} for w in (0, 1, 5, 20, 33, 38)]
    + [{"DimWeek.WeekIndex": set(range(0, 8))}, {"DimWeek.Phase": {"Before start"}}, {"DimWeek.Phase": {"Teaching"}}]
    + [{"DimWeek.WeekIndex": {12}, "DimModulePresentation.Module": {"DDD"}}, {"DimWeek.WeekIndex": {3}, "DimModulePresentation.MpKey": {"AAA-2013J"}}]
    + [{"AggActivityWeek.ActivityGroup": {g}} for g in ("Content", "Communication", "Homepage")]
    + [{"AggActivityWeek.ActivityGroup": {"Content", "Other"}, "DimModulePresentation.Module": {"FFF"}},
       {"AggActivityWeek.ActivityGroup": {"Assessment practice"}, "DimWeek.WeekIndex": {2}, "DimModulePresentation.MpKey": {"BBB-2014J"}}]
    + [{"AggOutcomeByGroup.Dimension": {d}} for d in ("gender", "disability", "age_band", "imd_band", "highest_education")]
    + [{"AggOutcomeByGroup.Dimension": {"age_band"}, "AggOutcomeByGroup.Band": {"55<="}},
       {"AggOutcomeByGroup.Dimension": {"imd_band"}, "AggOutcomeByGroup.Band": {"0-10%", "10-20%"}, "DimModulePresentation.Year": {2014}},
       {"AggOutcomeByGroup.Dimension": {"highest_education"}, "DimModulePresentation.MpKey": {"AAA-2013J"}},
       {"AggOutcomeByGroup.Suppressed": {1}}]
    + [{"OpsRunLog.Status": {"SUCCESS_WITH_WARNINGS"}}, {"OpsRunLog.Status": {"ROLLED_BACK"}}, {"OpsChecks.Kind": {"reconciliation"}},
       {"OpsChecks.Kind": {"custom", "volume"}, "DimWeek.WeekIndex": {10}}, {"OpsChecks.Kind": {"schema"}, "DimWeek.Phase": {"Before start"}}]
)


def to_dax(f):
    return {tuple(k.split(".")): v for k, v in f.items()}


def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)


def test_every_measure_has_a_reference_and_every_context_is_distinct(model):
    assert set(reference(model.frames, {})) == set(model.measures)
    assert len(model.measures) == 35 and len(CONTEXTS) >= 50
    assert len({json.dumps({k: sorted(v) for k, v in c.items()}, sort_keys=True) for c in CONTEXTS}) == len(CONTEXTS)


@pytest.mark.parametrize("i", range(len(CONTEXTS)))
def test_dax_measures_match_the_reference(model, ev, i):
    f = CONTEXTS[i]
    want = reference(model.frames, f)
    for name in model.measures:
        got = ev.measure(name, to_dax(f))
        assert close(got, want[name]), (name, f, got, want[name])


def test_the_contexts_exercise_every_measure(model, ev):
    """A measure that is blank or constant in every context would pass the comparison without being tested."""
    for name in model.measures:
        vals = {None if v is None else round(float(v), 9) for v in (ev.measure(name, to_dax(f)) for f in CONTEXTS)}
        assert len(vals - {None}) >= 2 or name == "Runs Stopped", name
    assert ev.measure("Runs Stopped") is None and ev.measure("Students", to_dax({"DimModulePresentation.MpKey": {"ZZZ-2099J"}})) is None


def test_measures_against_figures_computed_elsewhere(ev):
    pipeline = json.loads((ROOT / "results" / "pipeline.json").read_text())
    desc = json.loads((ROOT / "results" / "descriptive.json").read_text())
    assert ev.measure("Registrations") == pipeline["students"]["registrations"]
    assert ev.measure("Runs") == pipeline["runs"]["n"] and ev.measure("Checks Run") == pipeline["checks_all_runs"]["n"]
    assert ev.measure("Rows Loaded") == sum(pipeline["layers"][t] for t in pipeline["layers"] if t.startswith("bronze."))
    assert ev.measure("Peak Quarantine Rows") == pipeline["quarantine"]["peak_rows_during_replay"]
    assert close(ev.measure("At-risk Rate"), desc["outcomes"]["at_risk_share"])
    assert close(ev.measure("Withdrawal Rate"), desc["outcomes"]["Withdrawn"]["share"])


def test_withdrawal_curve_equals_kaplan_meier_where_nothing_is_withheld(ev, model):
    """Nobody is censored before week 33, so the plain share remaining is the Kaplan-Meier estimate."""
    desc = json.loads((ROOT / "results" / "descriptive.json").read_text())
    wd = model.frames["AggWithdrawal"]
    for day, week in ((28, 3), (56, 7), (84, 11), (168, 23)):          # day 28 is the first day of week 4
        if not wd[wd["WeekIndex"] == week]["Followed"].isna().any():
            got = ev.measure("Still Registered Share", {("DimWeek", "WeekIndex"): {week}})
            assert got == pytest.approx(desc["withdrawal"]["still_registered"][str(day)], abs=2e-3), day
    g = ev.measure("Still Registered Share", {("DimWeek", "WeekIndex"): {23}, ("DimModulePresentation", "Module"): {"GGG"}})
    assert g == pytest.approx(desc["withdrawal"]["by_module"]["GGG"]["day_168"], abs=5e-3)


def test_no_small_number_leaves_in_the_aggregate_tables(model):
    for table, cols in {"AggModuleWeek": ["EnrolledStudents"], "AggOutcome": ["Students", "Withdrawn", "Failed", "Passed"],
                        "AggOutcomeByGroup": ["Students", "AtRisk", "StudentsWithRate"], "AggWithdrawal": ["LeftByWeekEnd"]}.items():
        for c in cols:
            v = model.frames[table][c].dropna()
            assert (v >= 5).all(), (table, c)
    og = model.frames["AggOutcomeByGroup"]
    hidden = og[og["Students"].isna()].groupby(["MpKey", "Dimension"]).size()
    assert (hidden != 1).all()           # a single hidden cell could be recovered from the published total
    assert (og["Suppressed"] == og["Students"].isna().astype(int)).all()


def test_student_table_has_no_identifying_or_protected_columns(model):
    cols = set(model.frames["StudentSnapshot"].columns)
    assert not cols & {"id_student", "IdStudent", "Gender", "Disability", "ImdBand", "AgeBand", "Region", "FinalResult", "StudentKey"}
    assert model.frames["StudentSnapshot"]["SpKey"].str.fullmatch(r"[0-9a-f]{12}").all()
    assert set(model.frames["StudentSnapshot"]["MpKey"].str[-5:]) == {"2014J"}
    flagged = model.frames["StudentSnapshot"]
    assert (flagged.loc[flagged["Flagged"] == 0, "Reason1"] == "").all() and (flagged.loc[flagged["Flagged"] == 1, "Reason1"] != "").all()


def test_runs_stopped_counts_rejected_and_rolled_back_runs(model):
    """The replay of the real data has no stopped run, so the measure is exercised on an altered copy."""
    import copy
    m = copy.copy(model)
    m.frames = dict(model.frames)
    log = model.frames["OpsRunLog"].copy()
    log.loc[log.index[:2], "Status"] = ["ROLLED_BACK", "REJECTED_AT_INGEST"]
    log.loc[log.index[2], "Status"] = "SUCCESS"
    m.frames["OpsRunLog"] = log
    e = Evaluator(m)
    assert e.measure("Runs Stopped") == 2 == reference(m.frames, {})["Runs Stopped"]
    assert e.measure("Runs Stopped", {("DimWeek", "WeekIndex"): {-5}}) == 1
    assert e.measure("Runs Stopped", {("OpsRunLog", "Status"): {"SUCCESS"}}) == 2      # the measure's own filter wins


# --- roles ---------------------------------------------------------------------------------------------------

def test_lecturer_role_limits_every_related_table(model):
    m = model.with_role("Lecturer", "lecturer_bbb")
    own = {"BBB-2013B", "BBB-2013J", "BBB-2014B", "BBB-2014J"}
    for t in ("DimModulePresentation", "StudentSnapshot", "AggModuleWeek", "AggActivityWeek", "AggOutcome", "AggOutcomeByGroup", "AggWithdrawal"):
        assert set(m.frames[t]["MpKey"]) <= own and len(m.frames[t]) > 0, t
    assert set(m.frames["StudentSnapshot"]["MpKey"]) == {"BBB-2014J"}
    assert set(m.frames["SecLecturerModule"]["AppUser"]) == {"lecturer_bbb"}
    e = Evaluator(m)
    full = Evaluator(model)
    assert e.measure("Students") == full.measure("Students", {("DimModulePresentation", "Module"): {"BBB"}})
    assert e.measure("Registrations") == full.measure("Registrations", {("DimModulePresentation", "Module"): {"BBB"}})
    # a slicer on another module cannot widen what the role allows
    assert e.measure("Students", {("DimModulePresentation", "Module"): {"AAA"}}) is None
    two = model.with_role("Lecturer", "lecturer_two_modules")
    assert set(two.frames["DimModulePresentation"]["Module"]) == {"AAA", "GGG"}
    nobody = model.with_role("Lecturer", "someone_else")
    assert len(nobody.frames["StudentSnapshot"]) == 0 and len(nobody.frames["AggOutcome"]) == 0


def test_management_role_sees_aggregates_only(model):
    m = model.with_role("CollegeManagement", "principal")
    assert len(m.frames["StudentSnapshot"]) == 0 and len(m.frames["SecLecturerModule"]) == 0
    e = Evaluator(m)
    assert e.measure("Students") is None and e.measure("Flagged Students") is None and e.measure("Average Risk Score") is None
    assert e.measure("Registrations") == Evaluator(model).measure("Registrations")
    assert len(m.frames["AggOutcomeByGroup"]) == len(model.frames["AggOutcomeByGroup"])


def test_engineer_role_and_unknown_patterns(model):
    m = model.with_role("DataEngineer", "anyone")
    assert all(len(m.frames[t]) == len(model.frames[t]) for t in model.frames)
    with pytest.raises(KeyError):
        model.with_role("Auditor", "x")
    with pytest.raises(ValueError):
        model.role_rows("StudentSnapshot", "StudentSnapshot[Flagged] = 1", "x")
    assert set(model.roles) == {"Lecturer", "CollegeManagement", "DataEngineer"}


def test_role_assignments_match_the_sql_access_seed(model):
    sec = model.frames["SecLecturerModule"]
    assert len(sec) == 27 and set(sec["MpKey"]) == set(model.frames["DimModulePresentation"]["MpKey"])
    assert set(sec.loc[sec["AppUser"] == "lecturer_two_modules", "MpKey"].str[:3]) == {"AAA", "GGG"}


# --- the evaluator refuses what it does not know ---------------------------------------------------------------

def test_evaluator_rejects_dax_outside_its_subset(model):
    e = Evaluator(model)
    model.measures["tmp"] = type(next(iter(model.measures.values())))("tmp", "SUMX(StudentSnapshot, 1)", "StudentSnapshot")
    try:
        with pytest.raises(DaxError):
            e.measure("tmp")
    finally:
        del model.measures["tmp"]
    with pytest.raises(DaxError):
        e.measure("No Such Measure")


# --- generated files -----------------------------------------------------------------------------------------

@pytest.mark.golden
def test_powerbi_folder_matches_its_specification():
    assert build_powerbi.check() == []


def test_model_structure(model):
    assert len(model.meta.tables) == 12 and len(model.relationships) == 11
    for r in model.relationships:
        assert r.to_table in ("DimModulePresentation", "DimWeek")
        assert model.frames[r.to_table][r.to_column].is_unique
        assert set(model.frames[r.from_table][r.from_column].dropna()) <= set(model.frames[r.to_table][r.to_column])
    text = "".join(p.read_text(encoding="utf-8") for p in sorted(DEFINITION.rglob("*.tmdl")))
    tags = re.findall(r"lineageTag: (\S+)", text)
    assert len(tags) == len(set(tags)) > 100
    manifest = json.loads((DATA / "manifest.json").read_text())
    for name, t in model.meta.tables.items():
        assert manifest[t.csv_file[:-4]]["columns"] == [c.source_column for c in t.columns.values()], name


def test_report_visuals_reference_existing_fields(model):
    report = ROOT / "powerbi" / "LearningAnalytics.Report" / "definition"
    pages = json.loads((report / "pages" / "pages.json").read_text())["pageOrder"]
    assert pages == ["lecturer", "management", "pipeline"]
    n = 0
    for v in sorted(report.rglob("visual.json")):
        doc = json.loads(v.read_text(encoding="utf-8"))
        for role in doc["visual"].get("query", {}).get("queryState", {}).values():
            for p in role["projections"]:
                (kind, body), = p["field"].items()
                table, prop = body["Expression"]["SourceRef"]["Entity"], body["Property"]
                if kind == "Measure":
                    assert model.measures[prop].table == table, v
                else:
                    assert prop in model.frames[table].columns, v
                n += 1
    assert n >= 45
    for page in pages:
        doc = json.loads((report / "pages" / page / "page.json").read_text())
        names = {json.loads(p.read_text())["name"] for p in (report / "pages" / page / "visuals").rglob("visual.json")}
        for i in doc.get("visualInteractions", []):
            assert i["source"] in names and i["target"] in names
