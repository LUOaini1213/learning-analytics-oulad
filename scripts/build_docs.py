"""Render the generated documents from their templates and results/*.json.

Placeholders:
  {{file.json:dotted.path|fmt}}   fmt: .Nf  ,.Nf  +.Nf  pctN  int  str
  {{table:name}}                  tables and diagrams built from results files and the SQL
Usage: python scripts/build_docs.py [--check]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oulad import lineage, transform  # noqa: E402
from oulad.common import SOURCES  # noqa: E402

RESULTS = ROOT / "results"
TEMPLATES = ROOT / "docs" / "templates"
TARGETS = {
    ROOT / "README.md": TEMPLATES / "README.md",
    ROOT / "docs" / "runbook.md": TEMPLATES / "runbook.md",
    ROOT / "docs" / "lineage.md": TEMPLATES / "lineage.md",
    ROOT / "docs" / "model_explainer.md": TEMPLATES / "model_explainer.md",
    ROOT / "docs" / "lecturer_guide.md": TEMPLATES / "lecturer_guide.md",
}
PLACEHOLDER = re.compile(r"\{\{([^}]+)\}\}")
MODEL_NAMES = {"rule_recent_clicks_ranking": "Rule: fewest clicks in the last two weeks",
               "rule_previous_score": "Rule: lowest assessment score so far",
               "logistic_regression": "Logistic regression", "gradient_boosting": "Gradient boosting"}


def load_results() -> dict:
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(RESULTS.glob("*.json"))}


def lookup(data, path: str):
    for part in path.split("."):
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def fmt_value(v, fmt: str) -> str:
    if fmt == "str":
        return str(v)
    if fmt == "int":
        return f"{int(round(v)):,}"
    if fmt.startswith("pct"):
        return f"{v * 100:.{int(fmt[3:])}f}%"
    if fmt[:1] in ".,+":
        return format(v, fmt)
    raise ValueError(f"unknown format {fmt}")


def static_lineage_events() -> list[dict]:
    """Lineage as implied by the SQL files alone (what docs/lineage.md draws)."""
    stage = transform.stage_sql()
    events = [lineage.run_event("docs", f"bronze.{t}", [f"landing.{s}"], f"bronze.{t}", [], 0) for s, (t, _) in SOURCES.items()]
    for folder in ("silver", "gold", "features"):
        for p in sorted((transform.SQL / folder).glob("*.sql")):
            if p.stem.startswith("stage_"):
                continue
            target = f"{'gold' if folder == 'features' else folder}.{p.stem}"
            events.append(lineage.run_event("docs", target, lineage.referenced_tables(p.read_text(encoding="utf-8"), stage), target, [], 0))
    return events


def table(name: str, res: dict) -> str:
    pipe, drills, model, clus, desc = (res.get("pipeline.json"), res.get("drills.json"), res.get("model.json"),
                                       res.get("clustering.json"), res.get("descriptive.json"))
    if name == "lineage_mermaid":
        return "```mermaid\n" + lineage.mermaid(static_lineage_events()) + "\n```"
    if name == "lineage_edges":
        rows = ["| Table | Reads from |", "|---|---|"]
        for e in static_lineage_events():
            rows.append(f"| `{e['outputs'][0]['name']}` | {', '.join('`' + i['name'] + '`' for i in e['inputs'])} |")
        return "\n".join(rows)
    if name == "layers":
        rows = ["| Layer | Table | Rows |", "|---|---|---|"]
        for t, n in pipe["layers"].items():
            layer, tab = t.split(".")
            rows.append(f"| {layer} | `{tab}` | {n:,} |")
        return "\n".join(rows)
    if name == "reconciliation":
        rows = ["| Check | Left | Right | Agrees |", "|---|---|---|---|"]
        for r in pipe["reconciliation"]:
            rows.append(f"| `{r['check_id'].split(':')[1]}` | {int(r['left']):,} | {int(r['right']):,} | {'yes' if r['passed'] else 'NO'} |")
        return "\n".join(rows)
    if name == "warnings":
        rows = ["| Finding (warning-level check) | Rows |", "|---|---|"]
        text = {"score_recorded": "Submissions with no score", "submission_within_presentation": "Submissions dated more than 30 days after the presentation ended",
                "unregistered_is_withdrawn": "Students with an unregistration date whose result is not Withdrawn",
                "withdrawn_has_unregistration_date": "Students recorded as Withdrawn with no unregistration date",
                "registration_date_recorded": "Registrations with no registration date",
                "clicks_after_unregistration": "Student-material-days with clicks after the student's unregistration date"}
        for w in pipe["warnings_last_run"]:
            rows.append(f"| {text.get(w['check_id'].split(':')[-1], w['check_id'])} | {int(w['observed']):,} |")
        return "\n".join(rows)
    if name == "drills":
        rows = ["| Drill | What was broken | Caught by | Run status | Published tables untouched | Back to the clean state after the fix |",
                "|---|---|---|---|---|---|"]
        for d in drills["drills"]:
            by = ", ".join(f"`{c.split(':')[-1]}`" for c in d["detected_by"]) or "n/a"
            untouched = {True: "yes", False: "NO", None: "n/a"}[d["published_tables_untouched"]]
            rows.append(f"| {d['drill'].replace('_', ' ')} | {d['injected']} | {by} | {d['status']} | {untouched} | "
                        f"{'yes' if d['matches_clean_run_after_remediation'] else 'NO'} |")
        return "\n".join(rows)
    if name == "runbook_drills":
        out = []
        for d in drills["drills"]:
            out += [f"### {d['drill'].replace('_', ' ').capitalize()}", "",
                    f"- **Symptom rehearsed:** {d['injected']}.",
                    f"- **How it shows up:** run status `{d['status']}`; " + (
                        "failed check " + ", ".join(f"`{c}`" for c in d["detected_by"]) if d["detected_by"] else "no check fails") + ".",
                    f"- **What to do:** {d['remediation']}.",
                    f"- **Result in the drill:** after the fix the runs ended {', '.join('`' + s + '`' for s in d['status_after_remediation'])}; "
                    f"the lake {'matched' if d['matches_clean_run_after_remediation'] else 'DID NOT match'} a clean run.", ""]
        return "\n".join(out)
    if name == "model":
        rows = ["| Cut-off day | Model | PR-AUC (95% interval) | Top 10%: precision / recall | Top 20%: precision / recall | Brier score |",
                "|---|---|---|---|---|---|"]
        for c, r in model["cutoffs"].items():
            for m, label in MODEL_NAMES.items():
                v = r["models"][m]
                brier = f"{v['brier']:.3f}" if "brier" in v else "n/a"
                rows.append(f"| {c} | {label} | {v['pr_auc']:.3f} ({v['pr_auc_ci'][0]:.3f} to {v['pr_auc_ci'][1]:.3f}) | "
                            f"{v['top10']['precision']:.2f} / {v['top10']['recall']:.2f} | {v['top20']['precision']:.2f} / {v['top20']['recall']:.2f} | {brier} |")
        return "\n".join(rows)
    if name == "model_population":
        rows = ["| Cut-off day | Students scored (2014) | At risk among them | Training rows (2013) | Training rows dropped (student also in 2014) |",
                "|---|---|---|---|---|"]
        for c, r in model["cutoffs"].items():
            g = r["models"]["gradient_boosting"]
            rows.append(f"| {c} | {g['n']:,} | {g['prevalence'] * 100:.1f}% | {r['split']['train_rows']:,} | {r['split']['train_rows_dropped_for_overlap']:,} |")
        return "\n".join(rows)
    if name == "model_vs_baseline":
        rows = ["| Cut-off day | Best baseline | PR-AUC gain of gradient boosting (95% interval) | Gain in recall at top 20% (95% interval) |", "|---|---|---|---|"]
        for c, r in model["cutoffs"].items():
            a, b = r["gradient_boosting_vs_best_baseline"]["pr_auc"], r["gradient_boosting_vs_best_baseline"]["recall_top20"]
            rows.append(f"| {c} | {MODEL_NAMES[r['best_baseline']]} | {a['difference']:+.3f} ({a['ci_low']:+.3f} to {a['ci_high']:+.3f}) | "
                        f"{b['difference']:+.3f} ({b['ci_low']:+.3f} to {b['ci_high']:+.3f}) |")
        return "\n".join(rows)
    if name == "fairness":
        rows = ["| Attribute | Group | Students | At-risk rate | Share flagged | Of those at risk, share flagged |", "|---|---|---|---|---|---|"]
        for attr, f in model["cutoffs"]["28"]["fairness_gradient_boosting"].items():
            for g, v in f["groups"].items():
                rows.append(f"| {attr.replace('_', ' ')} | {g} | {v['n']:,} | {v['at_risk_rate'] * 100:.1f}% | {v['flag_rate'] * 100:.1f}% | {v['recall'] * 100:.1f}% |")
        return "\n".join(rows)
    if name == "fairness_gaps":
        rows = ["| Cut-off day | Gender | Disability | Age band | Deprivation |", "|---|---|---|---|---|"]
        for c, r in model["cutoffs"].items():
            f = r["fairness_gradient_boosting"]
            rows.append(f"| {c} | " + " | ".join(f"{f[k]['recall_gap'] * 100:.1f}" for k in ("gender", "disability", "age_band", "deprivation")) + " |")
        return "\n".join(rows)
    if name == "ablation":
        rows = ["| Cut-off day | PR-AUC, main model | PR-AUC with protected attributes added | Difference (95% interval) |", "|---|---|---|---|"]
        for c, r in model["cutoffs"].items():
            a = r["ablation_with_protected_attributes"]
            d = a["vs_main_model_pr_auc"]
            rows.append(f"| {c} | {r['models']['gradient_boosting']['pr_auc']:.3f} | {a['pr_auc']:.3f} | {d['difference']:+.3f} ({d['ci_low']:+.3f} to {d['ci_high']:+.3f}) |")
        return "\n".join(rows)
    if name == "by_module":
        r = model["cutoffs"]["28"]["by_module"]
        rows = ["| Module | Students | At risk | PR-AUC, gradient boosting | PR-AUC, logistic regression | Module seen in training |", "|---|---|---|---|---|---|"]
        for m, v in r.items():
            rows.append(f"| {m} | {v['n']:,} | {v['prevalence'] * 100:.1f}% | {v['pr_auc_gradient_boosting']:.3f} | {v['pr_auc_logistic_regression']:.3f} | {'yes' if v['in_training_data'] else 'no'} |")
        return "\n".join(rows)
    if name == "cluster_selection":
        rows = ["| Clusters (k) | Mean ARI over resamples | Worst resample | Silhouette |", "|---|---|---|---|"]
        for s in clus["selection"]:
            rows.append(f"| {s['k']} | {s['mean_ari']:.2f} | {s['min_ari']:.2f} | {s['silhouette']:.2f} |")
        return "\n".join(rows)
    if name == "clusters":
        rows = ["| Cluster | Share of 2013 students | Mean clicks per week, weeks -2 to 5 | At-risk rate 2013 | At-risk rate 2014 (95% interval) |", "|---|---|---|---|---|"]
        for c in clus["clusters"]:
            clicks = ", ".join(f"{x:.0f}" for x in c["mean_weekly_clicks_train"])
            rows.append(f"| {c['cluster']} | {c['share_train'] * 100:.1f}% | {clicks} | {c['at_risk_rate_train'] * 100:.1f}% | "
                        f"{c['at_risk_rate_test'] * 100:.1f}% ({c['at_risk_rate_test_ci'][0] * 100:.1f} to {c['at_risk_rate_test_ci'][1] * 100:.1f}) |")
        return "\n".join(rows)
    if name == "associations":
        rows = ["| Group compared with everyone else | Students in group | At-risk rate in group | At-risk rate, others | Ratio (95% interval) |", "|---|---|---|---|---|"]
        for a in desc["associations_day_28"]:
            if "risk_ratio" in a:
                rows.append(f"| {a['comparison'].capitalize()} | {a['n_in_group']:,} | {a['at_risk_rate_in_group'] * 100:.1f}% | {a['at_risk_rate_others'] * 100:.1f}% | "
                            f"{a['risk_ratio']:.2f} ({a['risk_ratio_ci'][0]:.2f} to {a['risk_ratio_ci'][1]:.2f}) |")
        return "\n".join(rows)
    if name == "withdrawal":
        w = desc["withdrawal"]
        rows = ["| Module | Students followed from day 0 | Still registered: day 28 | day 84 | day 168 |", "|---|---|---|---|---|"]
        for m, v in w["by_module"].items():
            rows.append(f"| {m} | {v['n']:,} | {v['day_28'] * 100:.1f}% | {v['day_84'] * 100:.1f}% | {v['day_168'] * 100:.1f}% |")
        rows.append(f"| All | {w['followed_from_day_0']:,} | {w['still_registered']['28'] * 100:.1f}% | {w['still_registered']['84'] * 100:.1f}% | {w['still_registered']['168'] * 100:.1f}% |")
        return "\n".join(rows)
    if name == "timeliness":
        rows = ["| Assessment type | Submissions | Submitted after the deadline | Median days against the deadline | Never submitted (of those due while registered) |", "|---|---|---|---|---|"]
        missed = {m["assessment_type"]: m for m in desc["assessment_timeliness"]["not_submitted"]}
        for t in desc["assessment_timeliness"]["by_type"]:
            ns = f"{missed[t['assessment_type']]['share'] * 100:.1f}%" if t["assessment_type"] in missed else "n/a"
            rows.append(f"| {t['assessment_type']} | {t['n']:,} | {t['share_late'] * 100:.1f}% | {t['median_days_late']:+.0f} | {ns} |")
        return "\n".join(rows)
    if name == "mutation_survivors":
        m = res["mutation.json"]["first"]
        return "\n".join(f"- `{s['file']}`: `{s['original'][:90]}`" for s in m["survivors"]) or "- none"
    if name == "digest":
        d = res["digest.json"]
        rows = ["| Prompt variant | Model | Digests | Passed the verifier | Invented number | Student identifier | Causal wording | Too long or empty |", "|---|---|---|---|---|---|---|---|"]
        for v in d["variants"]:
            f = v["failures"]
            rows.append(f"| {v['variant']} | {v['model']} | {v['n']} | {v['passed']} ({v['pass_rate'] * 100:.0f}%) | {f.get('number_not_in_source', 0)} | "
                        f"{f.get('student_identifier', 0)} | {f.get('causal_claim', 0)} | {f.get('length', 0)} |")
        return "\n".join(rows)
    raise KeyError(f"unknown table {name}")


def render(text: str, res: dict) -> str:
    def repl(m):
        body = m.group(1).strip()
        if body.startswith("table:"):
            return table(body[6:], res)
        ref, _, fmt = body.partition("|")
        fname, _, path = ref.partition(":")
        return fmt_value(lookup(res[fname], path), fmt or "str")
    return PLACEHOLDER.sub(repl, text)


def build_all() -> dict[Path, str]:
    res = load_results()
    return {target: render(template.read_text(encoding="utf-8"), res) for target, template in TARGETS.items() if template.exists()}


def main() -> None:
    stale = []
    for target, text in build_all().items():
        if "--check" in sys.argv:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                stale.append(target.relative_to(ROOT).as_posix())
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        sys.exit("stale: " + ", ".join(stale) + " (run python scripts/build_docs.py)")
    print("documents are up to date" if "--check" in sys.argv else f"{len(TARGETS)} documents written")


if __name__ == "__main__":
    main()
