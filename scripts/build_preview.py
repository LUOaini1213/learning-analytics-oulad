"""Render a static HTML preview of the three report pages, and the values a rendered report should show.

    python scripts/build_preview.py [--check]

Every number is produced by evaluating the DAX measures in the TMDL files (pbitest.dax) against
dashboard/data, in the filter context the corresponding Power BI visual would have. The preview is plain HTML
with inline SVG: it is NOT a screenshot of Power BI and the real visuals will look different.
Outputs: dashboard/preview.html and dashboard/expected_visual_values.csv.
"""
from __future__ import annotations

import csv
import html
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pbitest.dax import Evaluator  # noqa: E402
from pbitest.model import LoadedModel  # noqa: E402

OUT = ROOT / "dashboard"
MODULE, CUTOFF = "BBB-2014J", 28
COLOURS = ["#1b6ca8", "#d1495b", "#3a7d44", "#edae49", "#6b4e9b", "#00798c", "#8d6a4f"]


def fmt(v, f: str) -> str:
    if v is None:
        return "withheld / none"
    if "%" in f:
        return f"{v * 100:.{len(f.split('.')[1]) - 1 if '.' in f else 0}f}%"
    if "." in f:
        return f"{v:,.{len(f.split('.')[1])}f}"
    return f"{v:,.0f}"


class Page:
    def __init__(self, ev: Evaluator, model: LoadedModel, rows: list):
        self.ev, self.model, self.rows = ev, model, rows

    def value(self, visual: str, measure: str, ctx: dict, label: str):
        v = self.ev.measure(measure, ctx)
        self.rows.append([visual, measure, label, "" if v is None else repr(round(float(v), 6))])
        return v

    def cards(self, visual: str, measures: list[str], ctx: dict, label: str) -> str:
        cells = "".join(f"<div class='card'><div class='v'>{fmt(self.value(visual, m, ctx, label), self.model.measures[m].format_string)}</div>"
                        f"<div class='l'>{html.escape(m)}</div></div>" for m in measures)
        return f"<div class='cards'>{cells}</div>"


def line_chart(series: dict[str, list[tuple[float, float | None]]], title: str, pct: bool = True, w=560, h=230) -> str:
    pts = [(x, y) for s in series.values() for x, y in s if y is not None]
    if not pts:
        return f"<div class='chart'><h3>{html.escape(title)}</h3><p>No data</p></div>"
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(0.0 if not pct else min(p[1] for p in pts) * 0.95, min(p[1] for p in pts)), max(p[1] for p in pts) * 1.02
    sx = lambda x: 45 + (x - x0) / max(x1 - x0, 1e-9) * (w - 130)
    sy = lambda y: h - 25 - (y - y0) / max(y1 - y0, 1e-9) * (h - 40)
    out = [f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='{html.escape(title)}'>",
           f"<line x1='45' y1='{h - 25}' x2='{w - 85}' y2='{h - 25}' stroke='#999'/><line x1='45' y1='15' x2='45' y2='{h - 25}' stroke='#999'/>"]
    for t in (y0, (y0 + y1) / 2, y1):
        lab = f"{t * 100:.0f}%" if pct else f"{t:,.0f}"
        out.append(f"<text x='40' y='{sy(t) + 4:.1f}' text-anchor='end' class='ax'>{lab}</text>")
    for t in (x0, (x0 + x1) / 2, x1):
        out.append(f"<text x='{sx(t):.1f}' y='{h - 8}' text-anchor='middle' class='ax'>week {t:.0f}</text>")
    for i, (name, s) in enumerate(series.items()):
        path = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in s if y is not None)
        out.append(f"<polyline fill='none' stroke='{COLOURS[i % len(COLOURS)]}' stroke-width='2' points='{path}'/>")
        if len(series) > 1:
            out.append(f"<text x='{w - 80}' y='{22 + 14 * i}' class='ax' fill='{COLOURS[i % len(COLOURS)]}'>{html.escape(name)}</text>")
    return f"<div class='chart'><h3>{html.escape(title)}</h3>{''.join(out)}</svg></div>"


def bar_chart(items: list[tuple[str, float | None]], title: str, pct: bool = True, w=560) -> str:
    items = [(k, v) for k, v in items if v is not None]
    top = max((v for _, v in items), default=1) or 1
    h = 24 * len(items) + 10
    out = [f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='{html.escape(title)}'>"]
    for i, (k, v) in enumerate(items):
        y = 5 + 24 * i
        lab = f"{v * 100:.1f}%" if pct else f"{v:,.0f}"
        out.append(f"<text x='150' y='{y + 14}' text-anchor='end' class='ax'>{html.escape(str(k))}</text>"
                   f"<rect x='156' y='{y}' width='{v / top * (w - 240):.1f}' height='17' fill='{COLOURS[0]}'/>"
                   f"<text x='{160 + v / top * (w - 240):.1f}' y='{y + 14}' class='ax'>{lab}</text>")
    return f"<div class='chart'><h3>{html.escape(title)}</h3>{''.join(out)}</svg></div>"


def table(headers: list[str], rows: list[list], title: str) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{html.escape('' if c is None else str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class='tbl'><h3>{html.escape(title)}</h3><table><tr>{head}</tr>{body}</table></div>"


def build() -> tuple[str, str]:
    model = LoadedModel()
    ev, rows = Evaluator(model), []
    p = Page(ev, model, rows)
    f = model.frames
    weeks = sorted(int(w) for w in f["DimWeek"]["WeekIndex"] if w >= 0)
    mp = {("DimModulePresentation", "MpKey"): {MODULE}}
    snap = {**mp, ("StudentSnapshot", "CutoffDay"): {CUTOFF}}
    lec = [p.cards("lec_cards", ["Students", "Flagged Students", "Inactive Students", "Students Missing an Assessment", "Average Score"],
                   snap, f"{MODULE}, cut-off day {CUTOFF}")]
    active = [(w, p.value("lec_active_by_week", "Active Share", {**mp, ("DimWeek", "WeekIndex"): {w}}, f"{MODULE}, week {w}")) for w in weeks]
    groups = sorted(f["AggActivityWeek"]["ActivityGroup"].unique())
    mix = sorted([(g, p.value("lec_activity_mix", "Activity Share", {**mp, ("AggActivityWeek", "ActivityGroup"): {g}}, f"{MODULE}, {g}"))
                  for g in groups], key=lambda kv: -(kv[1] or 0))
    lec.append("<div class='row'>" + line_chart({"Active share": active}, "Share of enrolled students active online, by week")
               + bar_chart(mix, "Where the clicks went") + "</div>")
    ss = f["StudentSnapshot"]
    top = ss[(ss["MpKey"] == MODULE) & (ss["CutoffDay"] == CUTOFF)].sort_values(["RiskScore", "SpKey"], ascending=[False, True]).head(12)
    lec.append(table(["Student key", "Risk score", "Flagged", "Days since last active", "Clicks last 2 weeks", "Assessments missed",
                      "Average score", "Reasons"],
                     [[r.SpKey, f"{r.RiskScore:.3f}", r.Flagged, r.DaysSinceLastActive, r.ClicksLast2w, r.AssessmentsMissed,
                       "" if r.AvgScore != r.AvgScore else r.AvgScore, "; ".join(x for x in (r.Reason1, r.Reason2, r.Reason3) if x)]
                      for r in top.itertuples()], "Students, highest risk score first (first 12 rows)"))
    mgt = [p.cards("mgt_cards", ["Registrations", "Withdrawal Rate", "At-risk Rate", "Pass Rate", "Cells Withheld"], {}, "all")]
    modules = sorted(f["DimModulePresentation"]["Module"].unique())
    curve = {m: [(w, p.value("mgt_curve", "Still Registered Share", {("DimModulePresentation", "Module"): {m}, ("DimWeek", "WeekIndex"): {w}},
                             f"module {m}, week {w}")) for w in weeks if w <= 33] for m in modules}
    by_mp = sorted([(k, p.value("mgt_by_module", "At-risk Rate", {("DimModulePresentation", "MpKey"): {k}}, k))
                    for k in sorted(f["DimModulePresentation"]["MpKey"])], key=lambda kv: -(kv[1] or 0))
    mgt.append("<div class='row'>" + line_chart(curve, "Share still registered, by week and module")
               + bar_chart(by_mp, "At-risk rate by module-presentation") + "</div>")
    og = f["AggOutcomeByGroup"][["Dimension", "Band"]].drop_duplicates().sort_values(["Dimension", "Band"])
    grp = []
    for r in og.itertuples():
        ctx = {("AggOutcomeByGroup", "Dimension"): {r.Dimension}, ("AggOutcomeByGroup", "Band"): {r.Band}}
        label = f"{r.Dimension} = {r.Band}"
        grp.append([r.Dimension, r.Band, fmt(p.value("mgt_by_group", "Group Students", ctx, label), "#,##0"),
                    fmt(p.value("mgt_by_group", "Group At-risk Rate", ctx, label), "0.0%"),
                    fmt(p.value("mgt_by_group", "Cells Withheld", ctx, label), "#,##0").replace("withheld / none", "0")])
    mgt.append(table(["Attribute", "Group", "Students (shown cells)", "At-risk rate", "Cells withheld"], grp, "Outcomes by learner group"))
    ops = [p.cards("ops_cards", ["Runs", "Runs Stopped", "Rows Loaded", "Check Pass Rate", "Peak Quarantine Rows"], {}, "all")]
    all_weeks = sorted(int(w) for w in f["DimWeek"]["WeekIndex"])
    loaded = [(w, p.value("ops_rows_by_week", "Rows Loaded", {("DimWeek", "WeekIndex"): {w}}, f"week {w}")) for w in all_weeks]
    kinds = sorted([(k, p.value("ops_checks_by_kind", "Checks Run", {("OpsChecks", "Kind"): {k}}, k)) for k in sorted(f["OpsChecks"]["Kind"].unique())],
                   key=lambda kv: -(kv[1] or 0))
    ops.append("<div class='row'>" + line_chart({"Rows": loaded}, "Rows loaded per weekly run", pct=False)
               + bar_chart(kinds, "Checks run, by kind (all runs)", pct=False) + "</div>")
    wn = f["OpsWarnings"]
    ops.append(table(["Check", "Severity", "Observed"], [[r.CheckId, r.Severity, r.Observed] for r in wn.itertuples()],
                     "Checks not passed in the latest run"))
    css = ("body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;max-width:1220px}h2{border-bottom:2px solid #1b6ca8;"
           "padding-bottom:4px;margin-top:36px}.cards{display:flex;gap:12px;flex-wrap:wrap}.card{border:1px solid #ccc;border-radius:6px;"
           "padding:10px 16px;min-width:170px}.card .v{font-size:24px;font-weight:600}.card .l{font-size:12px;color:#555}"
           ".row{display:flex;gap:20px;flex-wrap:wrap}.chart{flex:1;min-width:480px}.ax{font-size:11px}table{border-collapse:collapse;"
           "font-size:12px}td,th{border:1px solid #ddd;padding:3px 8px;text-align:left}h3{font-size:14px}.note{background:#fff8e1;"
           "border:1px solid #edae49;padding:8px 12px;font-size:13px}")
    doc = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Learning analytics preview</title><style>{css}</style></head><body>"
           "<h1>Learning analytics on OULAD: static preview</h1>"
           "<p class='note'>Static preview generated by scripts/build_preview.py. It shows the values the Power BI report's measures "
           "produce for the same filters; it is not a rendering by Power BI, and the project has not been opened in Power BI Desktop. "
           "Data: Open University Learning Analytics Dataset (CC BY 4.0), UK distance learning, 2013 to 2014. Personal portfolio "
           "project; not affiliated with or endorsed by ITE or the Open University.</p>"
           f"<h2>1. My module this week (lecturer; {MODULE}, cut-off day {CUTOFF})</h2>" + "".join(lec)
           + "<h2>2. Programme overview (college management; aggregates with small cells withheld)</h2>" + "".join(mgt)
           + "<h2>3. Pipeline health and data quality (data engineer)</h2>" + "".join(ops) + "</body></html>\n")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["visual", "measure", "context", "value"])
    w.writerows(rows)
    return doc, buf.getvalue()


if __name__ == "__main__":
    doc, values = build()
    targets = {OUT / "preview.html": doc, OUT / "expected_visual_values.csv": values}
    if "--check" in sys.argv:
        bad = [p.name for p, c in targets.items() if not p.exists() or p.read_text(encoding="utf-8") != c]
        print("stale: " + ", ".join(bad) if bad else "preview is up to date")
        sys.exit(1 if bad else 0)
    for p, c in targets.items():
        p.write_text(c, encoding="utf-8", newline="\n")
    print(f"{len(values.splitlines()) - 1} visual values written")
