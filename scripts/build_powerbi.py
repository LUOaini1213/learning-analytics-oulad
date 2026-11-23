"""Write the Power BI project (PBIP) as text: the semantic model in TMDL and the report in PBIR.

    python scripts/build_powerbi.py [--check]

Everything under powerbi/ is generated from the specification in this file, and re-running must reproduce
the committed files byte for byte (--check, also a test). The project was written without Power BI Desktop
and has NOT been opened or rendered in it; see the README for what was and was not checked.
The helper functions for the report follow the author's earlier sg-transport-powerbi project (MIT).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "powerbi"
NAME = "LearningAnalytics"
NS = uuid.UUID("6f1d2c0a-3b56-4c0e-9d7e-1b2a3c4d5e6f")
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
DATA_URL = "https://raw.githubusercontent.com/LUOaini1213/learning-analytics-oulad/main/dashboard/data/"


def tag(*parts: str) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


# --- semantic model -------------------------------------------------------------------------------------
# table -> (csv file, description, [(column, dataType, hidden)], [(measure, DAX, format, description)])
I, S, D = "int64", "string", "double"
TABLES = {
    "DimModulePresentation": ("dim_module_presentation.csv", "One row per module-presentation (a module taught in one term).", [
        ("MpKey", S, False), ("Module", S, False), ("Presentation", S, False), ("Year", I, False),
        ("StartMonth", S, False), ("LengthDays", I, False), ("IsCurrentTerm", S, False)], []),
    "DimWeek": ("dim_week.csv", "Weeks on each presentation's own clock: week 0 is the first seven days.", [
        ("WeekIndex", I, False), ("WeekLabel", S, False), ("Phase", S, False)], []),
    "StudentSnapshot": ("student_snapshot.csv",
                        "One row per student per cut-off day (14, 28, 56, 84) for presentations that started in October "
                        "2014: what was known at the end of the day before the cut-off, the risk score from the logistic "
                        "model trained on 2013 presentations, and the reasons behind a flag. Pseudonymous keys; no "
                        "protected attributes and no final results.", [
        ("SpKey", S, False), ("MpKey", S, True), ("CutoffDay", I, False), ("ClicksTotal", I, False),
        ("ClicksLast2w", I, False), ("DaysSinceLastActive", I, False), ("AssessmentsDue", I, False),
        ("AssessmentsMissed", I, False), ("AvgScore", D, False), ("RiskScore", D, False), ("Flagged", I, False),
        ("Reason1", S, False), ("Reason2", S, False), ("Reason3", S, False)], [
        ("Students", "COUNTROWS(StudentSnapshot)", "#,##0", "Students in the snapshot (registered and not withdrawn at the cut-off)."),
        ("Flagged Students", "CALCULATE(COUNTROWS(StudentSnapshot), StudentSnapshot[Flagged] = 1)", "#,##0",
         "Students in the top 20% of risk scores in their class at the cut-off."),
        ("Flagged Share", "DIVIDE([Flagged Students], [Students])", "0.0%", "Flagged students as a share of the snapshot."),
        ("Inactive Students", "CALCULATE(COUNTROWS(StudentSnapshot), StudentSnapshot[ClicksLast2w] = 0)", "#,##0",
         "Students with no clicks in the two weeks before the cut-off."),
        ("Inactive Share", "DIVIDE([Inactive Students], [Students])", "0.0%", "Inactive students as a share of the snapshot."),
        ("Students Missing an Assessment", "CALCULATE(COUNTROWS(StudentSnapshot), StudentSnapshot[AssessmentsMissed] > 0)", "#,##0",
         "Students with at least one assessment due before the cut-off and not submitted."),
        ("Average Score", "AVERAGE(StudentSnapshot[AvgScore])", "0.0", "Mean of the students' average assessment scores so far (students with a score)."),
        ("Average Risk Score", "AVERAGE(StudentSnapshot[RiskScore])", "0.000", "Mean model score; a ranking aid, not a probability to quote to a student."),
        ("Median Days Since Last Active", "PERCENTILE.INC(StudentSnapshot[DaysSinceLastActive], 0.5)", "0.0", "Median days since a student was last seen online."),
    ]),
    "AggModuleWeek": ("agg_module_week.csv", "Per module-presentation and week: students enrolled at the start of the week, students "
                      "active online, and clicks. Cells based on fewer than 5 students are blank.", [
        ("MpKey", S, True), ("WeekIndex", I, True), ("EnrolledStudents", I, True), ("ActiveStudents", I, True), ("Clicks", I, True)], [
        ("Enrolled Students", "SUM(AggModuleWeek[EnrolledStudents])", "#,##0", "Students registered at the start of the week (sum over the weeks and classes in view)."),
        ("Active Students", "SUM(AggModuleWeek[ActiveStudents])", "#,##0", "Students with at least one click in the week."),
        ("Clicks", "SUM(AggModuleWeek[Clicks])", "#,##0", "Clicks on the online learning environment."),
        ("Active Share", "DIVIDE([Active Students], [Enrolled Students])", "0.0%", "Active students divided by enrolled students."),
        ("Clicks per Active Student", "DIVIDE([Clicks], [Active Students])", "#,##0.0", "Clicks divided by active students."),
    ]),
    "AggActivityWeek": ("agg_activity_week.csv", "Clicks per module-presentation, week and activity group. Blank below 5 students.", [
        ("MpKey", S, True), ("WeekIndex", I, True), ("ActivityGroup", S, False), ("Clicks", I, True)], [
        ("Activity Clicks", "SUM(AggActivityWeek[Clicks])", "#,##0", "Clicks by activity group."),
        ("Activity Share", 'DIVIDE([Activity Clicks], CALCULATE([Activity Clicks], AggActivityWeek[ActivityGroup] <> ""))', "0.0%",
         "Share of clicks that went to the activity group(s) in view."),
    ]),
    "AggOutcome": ("agg_outcome.csv", "Final results per module-presentation. Counts below 5 are blank.", [
        ("MpKey", S, True), ("Students", I, True), ("Withdrawn", I, True), ("Failed", I, True), ("Passed", I, True), ("Suppressed", I, True)], [
        ("Registrations", "SUM(AggOutcome[Students])", "#,##0", "Students registered on the module-presentation."),
        ("Withdrawn", "SUM(AggOutcome[Withdrawn])", "#,##0", "Students whose final result is Withdrawn."),
        ("Failed", "SUM(AggOutcome[Failed])", "#,##0", "Students whose final result is Fail."),
        ("Passed", "SUM(AggOutcome[Passed])", "#,##0", "Students whose final result is Pass or Distinction."),
        ("Withdrawal Rate", "DIVIDE([Withdrawn], [Registrations])", "0.0%", "Withdrawn divided by registrations."),
        ("At-risk Rate", "DIVIDE([Withdrawn] + [Failed], [Registrations])", "0.0%", "Withdrawn or failed, divided by registrations."),
        ("Pass Rate", "DIVIDE([Passed], [Registrations])", "0.0%", "Passed divided by registrations."),
    ]),
    "AggOutcomeByGroup": ("agg_outcome_by_group.csv", "Students and at-risk students per module-presentation by one learner attribute at a "
                          "time. Small cells are blank, and one more cell is blanked where a single hidden cell could be worked out from the total.", [
        ("MpKey", S, True), ("Dimension", S, False), ("Band", S, False), ("Students", I, True), ("AtRisk", I, True),
        ("StudentsWithRate", I, True), ("Suppressed", I, True)], [
        ("Group Students", "SUM(AggOutcomeByGroup[Students])", "#,##0", "Students in the group(s) in view, where the count may be shown."),
        ("Group At-risk Rate", "DIVIDE(SUM(AggOutcomeByGroup[AtRisk]), SUM(AggOutcomeByGroup[StudentsWithRate]))", "0.0%",
         "Withdrawn or failed as a share of students, over the cells where both counts may be shown."),
        ("Cells Withheld", "CALCULATE(COUNTROWS(AggOutcomeByGroup), AggOutcomeByGroup[Suppressed] = 1)", "#,##0",
         "Number of group cells whose student count is withheld."),
    ]),
    "AggWithdrawal": ("agg_withdrawal.csv", "Students followed from day 0 and how many had left by the end of each week (weeks 0 to 33). "
                      "Cells where fewer than 5 had left, or fewer than 5 remained, are blank.", [
        ("MpKey", S, True), ("WeekIndex", I, True), ("Followed", I, True), ("LeftByWeekEnd", I, True)], [
        ("Still Registered Share",
         "\n\t\t\tVAR gone = DIVIDE(SUM(AggWithdrawal[LeftByWeekEnd]), SUM(AggWithdrawal[Followed]))\n\t\t\tRETURN IF(ISBLANK(gone), BLANK(), 1 - gone)",
         "0.0%", "Share of the students registered on day 0 who had not unregistered by the end of the week."),
    ]),
    "OpsRunLog": ("ops_run_log.csv", "One row per weekly pipeline run.", [
        ("RunId", S, False), ("WeekIndex", I, True), ("Status", S, False), ("DurationSeconds", D, False), ("RowsLoaded", I, False),
        ("QuarantineRows", I, False), ("WeeksRecomputed", I, False), ("Checks", I, False), ("FailedError", I, False), ("FailedWarn", I, False)], [
        ("Runs", "COUNTROWS(OpsRunLog)", "#,##0", "Weekly pipeline runs."),
        ("Runs Stopped", 'CALCULATE(COUNTROWS(OpsRunLog), OpsRunLog[Status] IN {"REJECTED_AT_INGEST", "ROLLED_BACK"})', "#,##0",
         "Runs rejected at the ingest gate or rolled back after a failed check."),
        ("Rows Loaded", "SUM(OpsRunLog[RowsLoaded])", "#,##0", "Source rows landed in bronze."),
        ("Run Seconds", "SUM(OpsRunLog[DurationSeconds])", "#,##0.0", "Total run time."),
        ("Peak Quarantine Rows", "MAX(OpsRunLog[QuarantineRows])", "#,##0", "Largest number of rejected rows waiting after any run."),
    ]),
    "OpsChecks": ("ops_checks.csv", "Data-quality checks per week and kind of check.", [
        ("WeekIndex", I, True), ("Kind", S, False), ("Checks", I, True), ("Failed", I, True)], [
        ("Checks Run", "SUM(OpsChecks[Checks])", "#,##0", "Contract checks evaluated."),
        ("Checks Failed", "SUM(OpsChecks[Failed])", "#,##0", "Checks that did not pass (warnings included)."),
        ("Check Pass Rate", "DIVIDE([Checks Run] - [Checks Failed], [Checks Run])", "0.00%", "Share of checks that passed."),
    ]),
    "OpsWarnings": ("ops_warnings.csv", "Checks that did not pass in the latest run.", [
        ("CheckId", S, False), ("Severity", S, False), ("Observed", S, False), ("Detail", S, False)], []),
    "SecLecturerModule": ("sec_lecturer_module.csv", "SYNTHETIC teaching assignments used by the Lecturer role: which sign-in name may see which module-presentations.", [
        ("AppUser", S, False), ("MpKey", S, False)], []),
}
RELATIONSHIPS = [("StudentSnapshot", "MpKey", "DimModulePresentation", "MpKey"),
                 ("AggModuleWeek", "MpKey", "DimModulePresentation", "MpKey"), ("AggModuleWeek", "WeekIndex", "DimWeek", "WeekIndex"),
                 ("AggActivityWeek", "MpKey", "DimModulePresentation", "MpKey"), ("AggActivityWeek", "WeekIndex", "DimWeek", "WeekIndex"),
                 ("AggOutcome", "MpKey", "DimModulePresentation", "MpKey"),
                 ("AggOutcomeByGroup", "MpKey", "DimModulePresentation", "MpKey"),
                 ("AggWithdrawal", "MpKey", "DimModulePresentation", "MpKey"), ("AggWithdrawal", "WeekIndex", "DimWeek", "WeekIndex"),
                 ("OpsRunLog", "WeekIndex", "DimWeek", "WeekIndex"), ("OpsChecks", "WeekIndex", "DimWeek", "WeekIndex")]
# role -> (description, {table: row filter in DAX})
ROLES = {
    "Lecturer": ("Sees student rows and aggregates only for the module-presentations assigned to the signed-in user.", {
        "DimModulePresentation": "CONTAINS(SecLecturerModule, SecLecturerModule[AppUser], USERNAME(), SecLecturerModule[MpKey], DimModulePresentation[MpKey])",
        "SecLecturerModule": "SecLecturerModule[AppUser] = USERNAME()"}),
    "CollegeManagement": ("Sees aggregates only: every student-level row is filtered out.", {
        "StudentSnapshot": "FALSE()", "SecLecturerModule": "FALSE()"}),
    "DataEngineer": ("Sees everything.", {}),
}
M_TYPES = {I: "Int64.Type", S: "type text", D: "type number"}


def q(name: str) -> str:
    return f"'{name}'" if not name.replace("_", "").isalnum() else name


def table_tmdl(name: str) -> str:
    csv, desc, columns, measures = TABLES[name]
    out = [f"/// {desc}", f"table {name}", f"\tlineageTag: {tag('table', name)}", ""]
    for m, dax, fmt, mdesc in measures:
        out += [f"\t/// {mdesc}", f"\tmeasure {q(m)} = {dax}", f"\t\tformatString: {fmt}", f"\t\tlineageTag: {tag('measure', m)}", ""]
    for col, dtype, hidden in columns:
        out += [f"\tcolumn {col}", f"\t\tdataType: {dtype}"] + (["\t\tisHidden"] if hidden else [])
        if dtype != S:
            out.append("\t\tformatString: 0" if dtype == I else "\t\tformatString: 0.000")
        out += [f"\t\tlineageTag: {tag('column', name, col)}", "\t\tsummarizeBy: none", f"\t\tsourceColumn: {col}", ""]
    types = ", ".join('{"%s", %s}' % (c, M_TYPES[t]) for c, t, _ in columns)
    out += [f"\tpartition {name} = m", "\t\tmode: import", "\t\tsource =", "\t\t\t\tlet", "\t\t\t\t\tSource = Csv.Document(",
            f'\t\t\t\t\t\tif Text.StartsWith(DataFolder, "http") then Web.Contents(DataFolder & "{csv}") else File.Contents(DataFolder & "{csv}"),',
            "\t\t\t\t\t\t[Delimiter = \",\", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]", "\t\t\t\t\t),",
            "\t\t\t\t\tPromoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
            f"\t\t\t\t\tTyped = Table.TransformColumnTypes(Promoted, {{{types}}}, \"en-US\")", "\t\t\t\tin", "\t\t\t\t\tTyped", "",
            "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(out)


def model_files() -> dict[str, str]:
    files = {f"definition/tables/{t}.tmdl": table_tmdl(t) for t in TABLES}
    files["definition/database.tmdl"] = "database\n\tcompatibilityLevel: 1606\n"
    order = json.dumps(["DataFolder"] + list(TABLES), separators=(",", ":"))
    files["definition/model.tmdl"] = "\n".join(
        ["model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3", "\tdiscourageImplicitMeasures",
         "\tsourceQueryCulture: en-US", "\tdataAccessOptions", "\t\tlegacyRedirects", "\t\treturnErrorValuesAsNull", "",
         "annotation __PBI_TimeIntelligenceEnabled = 0", "", f"annotation PBI_QueryOrder = {order}", "",
         'annotation PBI_ProTooling = ["DevMode"]', ""] + [f"ref table {t}" for t in TABLES] + [""] + [f"ref role {r}" for r in ROLES] + [""])
    files["definition/expressions.tmdl"] = "\n".join(
        ["/// Folder or URL that holds the CSV extracts, ending with a slash. The default reads the files committed to",
         "/// the repository; for a local clone set it to the dashboard/data folder.",
         f'expression DataFolder = "{DATA_URL}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
         f"\tlineageTag: {tag('expression', 'DataFolder')}", "", "\tannotation PBI_ResultType = Text", ""])
    rel = []
    for ft, fc, tt, tc in RELATIONSHIPS:
        rel += [f"relationship {tag('relationship', ft, fc, tt)}", f"\tfromColumn: {ft}.{fc}", f"\ttoColumn: {tt}.{tc}", ""]
    files["definition/relationships.tmdl"] = "\n".join(rel)
    for role, (desc, perms) in ROLES.items():
        lines = [f"/// {desc}", f"role {role}", "\tmodelPermission: read", ""]
        for t, dax in perms.items():
            lines += [f"\ttablePermission {t} = {dax}", ""]
        files[f"definition/roles/{role}.tmdl"] = "\n".join(lines)
    files["definition.pbism"] = json.dumps({"version": "4.2", "settings": {}}, indent=2) + "\n"
    files[".platform"] = json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": tag("platform", "model")}}, indent=2) + "\n"
    return files


# --- report ---------------------------------------------------------------------------------------------

def lit(value):
    return {"expr": {"Literal": {"Value": value}}}


def text(s):
    return lit("'" + s.replace("'", "''") + "'")


def field(kind, entity, prop):
    return {kind: {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def col(entity, prop):
    return ("Column", entity, prop)


def msr(prop):
    entity = next(t for t, spec in TABLES.items() if any(m[0] == prop for m in spec[3]))
    return ("Measure", entity, prop)


def projection(f, active=False):
    kind, entity, prop = f
    p = {"field": field(kind, entity, prop), "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if active:
        p["active"] = True
    return p


def title_objects(title):
    return {"title": [{"properties": {"show": lit("true"), "text": text(title)}}]}


def chart(vtype, category, values, title, sort=None, series=None):
    roles = {"Category": {"projections": [projection(category, active=True)]},
             "Y": {"projections": [projection(v) for v in values]}}
    if series:
        roles["Series"] = {"projections": [projection(series)]}
    v = {"visualType": vtype, "query": {"queryState": roles}, "visualContainerObjects": title_objects(title)}
    if sort:
        kind, entity, prop = sort[0]
        v["query"]["sortDefinition"] = {"sort": [{"field": field(kind, entity, prop), "direction": sort[1]}], "isDefaultSort": False}
    return v


def card(values, title):
    return {"visualType": "cardVisual", "query": {"queryState": {"Data": {"projections": [projection(v) for v in values]}}},
            "visualContainerObjects": title_objects(title)}


def table(values, title, sort):
    kind, entity, prop = sort[0]
    return {"visualType": "tableEx",
            "query": {"queryState": {"Values": {"projections": [projection(v) for v in values]}},
                      "sortDefinition": {"sort": [{"field": field(kind, entity, prop), "direction": sort[1]}], "isDefaultSort": False}},
            "visualContainerObjects": title_objects(title)}


def slicer(column, title, selected=None, quote=True):
    kind, entity, prop = column
    objects = {"data": [{"properties": {"mode": text("Dropdown")}}]}
    if selected is not None:
        value = ("'" + selected + "'") if quote else f"{selected}L"
        objects["general"] = [{"properties": {"filter": {"filter": {
            "Version": 2, "From": [{"Name": "s", "Entity": entity, "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": prop}}],
                "Values": [[{"Literal": {"Value": value}}]]}}}]}}}}]
    return {"visualType": "slicer", "query": {"queryState": {"Values": {"projections": [projection(column, True)]}}},
            "objects": objects, "visualContainerObjects": title_objects(title)}


def textbox(lines):
    paragraphs = []
    for i, line in enumerate(lines):
        style = {"fontWeight": "bold", "fontSize": "16pt"} if i == 0 else {"fontSize": "10pt"}
        paragraphs.append({"textRuns": [{"value": line, "textStyle": style}]})
    return {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]}}


MP, WK, SS, MW, AW, OG, RL, CK, WN = ("DimModulePresentation", "DimWeek", "StudentSnapshot", "AggModuleWeek", "AggActivityWeek",
                                     "AggOutcomeByGroup", "OpsRunLog", "OpsChecks", "OpsWarnings")
PAGES = [
    {"name": "lecturer", "displayName": "My module this week", "visuals": [
        ("lec_header", 20, 10, 620, 70, textbox([
            "My module this week",
            "Students registered at the cut-off, with what was known the day before. A flag marks the fifth of the class "
            "the model ranks highest; it is a prompt to get in touch, not a prediction about a person."])),
        ("lec_module_slicer", 660, 10, 290, 70, slicer(col(MP, "MpKey"), "Module-presentation", selected="BBB-2014J")),
        ("lec_cutoff_slicer", 970, 10, 290, 70, slicer(col(SS, "CutoffDay"), "Cut-off day", selected="28", quote=False)),
        ("lec_cards", 20, 90, 1240, 120, card([msr("Students"), msr("Flagged Students"), msr("Inactive Students"),
                                               msr("Students Missing an Assessment"), msr("Average Score")], "Class at the cut-off")),
        ("lec_active_by_week", 20, 220, 600, 230, chart("lineChart", col(WK, "WeekIndex"), [msr("Active Share")],
                                                        "Share of enrolled students active online, by week", sort=(col(WK, "WeekIndex"), "Ascending"))),
        ("lec_activity_mix", 640, 220, 620, 230, chart("clusteredBarChart", col(AW, "ActivityGroup"), [msr("Activity Share")],
                                                       "Where the clicks went", sort=(msr("Activity Share"), "Descending"))),
        ("lec_students", 20, 460, 1240, 240, table([
            col(SS, "SpKey"), col(SS, "RiskScore"), col(SS, "Flagged"), col(SS, "DaysSinceLastActive"), col(SS, "ClicksLast2w"),
            col(SS, "AssessmentsMissed"), col(SS, "AvgScore"), col(SS, "Reason1"), col(SS, "Reason2"), col(SS, "Reason3")],
            "Students, highest risk score first", sort=(col(SS, "RiskScore"), "Descending")))],
     "interactions": [("lec_cutoff_slicer", "lec_active_by_week", "NoFilter"), ("lec_cutoff_slicer", "lec_activity_mix", "NoFilter")]},
    {"name": "management", "displayName": "Programme overview", "visuals": [
        ("mgt_header", 20, 10, 900, 70, textbox([
            "Programme overview",
            "Aggregates only. A blank means the figure rests on fewer than five students and is withheld."])),
        ("mgt_year_slicer", 940, 10, 320, 70, slicer(col(MP, "Year"), "Year")),
        ("mgt_cards", 20, 90, 1240, 120, card([msr("Registrations"), msr("Withdrawal Rate"), msr("At-risk Rate"), msr("Pass Rate"),
                                               msr("Cells Withheld")], "All module-presentations in view")),
        ("mgt_curve", 20, 220, 620, 240, chart("lineChart", col(WK, "WeekIndex"), [msr("Still Registered Share")],
                                               "Share still registered, by week and module", sort=(col(WK, "WeekIndex"), "Ascending"), series=col(MP, "Module"))),
        ("mgt_by_module", 660, 220, 600, 240, chart("clusteredBarChart", col(MP, "MpKey"), [msr("Withdrawal Rate"), msr("At-risk Rate")],
                                                    "Withdrawal and at-risk rate by module-presentation", sort=(msr("At-risk Rate"), "Descending"))),
        ("mgt_by_group", 20, 470, 1240, 230, table([col(OG, "Dimension"), col(OG, "Band"), msr("Group Students"), msr("Group At-risk Rate"),
                                                    msr("Cells Withheld")], "Outcomes by learner group", sort=(col(OG, "Dimension"), "Ascending")))],
     "interactions": []},
    {"name": "pipeline", "displayName": "Pipeline health", "visuals": [
        ("ops_header", 20, 10, 1240, 70, textbox([
            "Pipeline health and data quality",
            "One run per week of the replay. Checks come from the data contracts; rejected rows wait in quarantine."])),
        ("ops_cards", 20, 90, 1240, 120, card([msr("Runs"), msr("Runs Stopped"), msr("Rows Loaded"), msr("Check Pass Rate"),
                                               msr("Peak Quarantine Rows")], "All runs")),
        ("ops_rows_by_week", 20, 220, 620, 230, chart("clusteredColumnChart", col(WK, "WeekIndex"), [msr("Rows Loaded")],
                                                      "Rows loaded per weekly run", sort=(col(WK, "WeekIndex"), "Ascending"))),
        ("ops_checks_by_kind", 660, 220, 600, 230, chart("clusteredBarChart", col(CK, "Kind"), [msr("Checks Run"), msr("Checks Failed")],
                                                        "Checks by kind", sort=(msr("Checks Run"), "Descending"))),
        ("ops_runs", 20, 460, 700, 240, table([col(RL, "RunId"), col(RL, "Status"), col(RL, "DurationSeconds"), col(RL, "RowsLoaded"),
                                               col(RL, "QuarantineRows"), col(RL, "FailedWarn")], "Runs", sort=(col(RL, "RunId"), "Ascending"))),
        ("ops_warnings", 740, 460, 520, 240, table([col(WN, "CheckId"), col(WN, "Severity"), col(WN, "Observed")],
                                                   "Checks not passed in the latest run", sort=(col(WN, "CheckId"), "Ascending")))],
     "interactions": []},
]
REPORT = {
    "$schema": f"{SCHEMA}/report/3.0.0/schema.json",
    "themeCollection": {"baseTheme": {"name": "CY24SU10", "reportVersionAtImport": {"visual": "1.8.97", "report": "2.0.97", "page": "1.3.97"},
                                      "type": "SharedResources"}},
    "resourcePackages": [{"name": "SharedResources", "type": "SharedResources",
                          "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json", "type": "BaseTheme"}]}],
    "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True,
                 "useEnhancedTooltips": True},
}


def dump(obj) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def report_files() -> dict[str, str]:
    files = {"definition/version.json": dump({"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"}),
             "definition/report.json": dump(REPORT),
             "definition/pages/pages.json": dump({"$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
                                                  "pageOrder": [p["name"] for p in PAGES], "activePageName": PAGES[0]["name"]}),
             "definition.pbir": dump({"version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}}),
             ".platform": dump({
                 "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
                 "metadata": {"type": "Report", "displayName": NAME},
                 "config": {"version": "2.0", "logicalId": tag("platform", "report")}})}
    for p in PAGES:
        page = {"$schema": f"{SCHEMA}/page/2.0.0/schema.json", "name": p["name"], "displayName": p["displayName"],
                "displayOption": "FitToPage", "height": 720, "width": 1280}
        if p["interactions"]:
            page["visualInteractions"] = [{"source": s, "target": t, "type": k} for s, t, k in p["interactions"]]
        files[f"definition/pages/{p['name']}/page.json"] = dump(page)
        for z, (name, x, y, w, h, visual) in enumerate(p["visuals"]):
            files[f"definition/pages/{p['name']}/visuals/{name}/visual.json"] = dump({
                "$schema": f"{SCHEMA}/visualContainer/2.4.0/schema.json", "name": name,
                "position": {"x": x, "y": y, "z": z * 1000, "height": h, "width": w, "tabOrder": z * 1000}, "visual": visual})
    return files


def all_files() -> dict[str, str]:
    files = {f"{NAME}.pbip": dump({"version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                                   "settings": {"enableAutoRecovery": True}})}
    files.update({f"{NAME}.SemanticModel/{k}": v for k, v in model_files().items()})
    files.update({f"{NAME}.Report/{k}": v for k, v in report_files().items()})
    return files


def build(out: Path = OUT) -> None:
    theme = out / f"{NAME}.Report" / "StaticResources"
    keep = None
    if theme.exists():
        keep = Path(tempfile.mkdtemp()) / "StaticResources"
        shutil.copytree(theme, keep)
    if out.exists():
        shutil.rmtree(out)
    for rel, content in all_files().items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    if keep:
        shutil.copytree(keep, theme)


def check(out: Path = OUT) -> list[str]:
    problems = []
    expected = all_files()
    for rel, content in expected.items():
        p = out / rel
        if not p.exists():
            problems.append(f"missing {rel}")
        elif p.read_text(encoding="utf-8") != content:
            problems.append(f"differs {rel}")
    present = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and "StaticResources" not in p.parts}
    problems += [f"unexpected {r}" for r in sorted(present - set(expected))]
    return problems


if __name__ == "__main__":
    if "--check" in sys.argv:
        bad = check()
        print("\n".join(bad) or "powerbi/ matches the specification")
        sys.exit(1 if bad else 0)
    build()
