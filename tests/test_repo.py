"""Repository-level checks: every number in the documents traces to results, generated files are fresh,
the prose claims still hold, provenance and disclaimers are in place, and nothing private is committed."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_docs, build_preview, mutate

ROOT = build_docs.ROOT
NUM = re.compile(r"(?<![A-Za-z0-9_.,/\-])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")
RES = build_docs.load_results()


def tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    return [ROOT / p for p in out if p]


def _numbers(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _numbers(v)
    elif isinstance(x, list):
        for v in x:
            yield from _numbers(v)
    elif isinstance(x, (int, float)) and not isinstance(x, bool):
        yield float(x)
    elif isinstance(x, str) and re.fullmatch(r"-?\d+(\.\d+)?", x):
        yield float(x)


@pytest.mark.golden
def test_generated_documents_are_up_to_date():
    for target, text in build_docs.build_all().items():
        assert target.read_text(encoding="utf-8") == text, f"{target.name} is stale: run python scripts/build_docs.py"


@pytest.mark.golden
def test_preview_is_up_to_date():
    doc, values = build_preview.build()
    assert (ROOT / "dashboard" / "preview.html").read_text(encoding="utf-8") == doc
    assert (ROOT / "dashboard" / "expected_visual_values.csv").read_text(encoding="utf-8") == values


def test_readme_drift_is_detected(tmp_path):
    """Change one result and the rendered README must change with it."""
    res = json.loads(json.dumps(RES))
    template = (build_docs.TEMPLATES / "README.md").read_text(encoding="utf-8")
    before = build_docs.render(template, res)
    res["pipeline.json"]["runs"]["n"] += 1
    res["model.json"]["cutoffs"]["28"]["models"]["gradient_boosting"]["pr_auc"] += 0.01
    after = build_docs.render(template, res)
    assert before != after and before == (ROOT / "README.md").read_text(encoding="utf-8")
    with pytest.raises(KeyError):
        build_docs.render("{{pipeline.json:no.such.key|int}}", res)
    with pytest.raises(ValueError):
        build_docs.render("{{pipeline.json:runs.n|bogus}}", res)


def test_templates_contain_no_hand_typed_results():
    """Outside placeholders, the templates may hold only structural numbers (days, weeks, thresholds, versions)."""
    allowed = {"0", "1", "2", "3", "4", "5", "7", "10", "14", "20", "24", "28", "30", "33", "45", "55", "56", "84", "95",
               "100", "160", "168", "450", "0.4", "2.5", "0.01", "0.80", "0.7", "70%", "5%", "10%", "20%", "50%", "95%",
               "2013", "2014", "2017", "256", "-2", "2-0-2", "40", "120", "4.0"}
    for t in sorted(build_docs.TEMPLATES.glob("*.md")):
        text = build_docs.PLACEHOLDER.sub("", t.read_text(encoding="utf-8"))
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        found = {n for n in NUM.findall(text)} - allowed
        assert not found, (t.name, sorted(found))


def test_every_placeholder_resolves_and_formats():
    for t in sorted(build_docs.TEMPLATES.glob("*.md")):
        text = t.read_text(encoding="utf-8")
        assert len(build_docs.PLACEHOLDER.findall(text)) >= 2, t.name
        assert "{{" not in build_docs.render(text, RES)
    assert build_docs.fmt_value(0.1234, "pct1") == "12.3%" and build_docs.fmt_value(1234.5, "int") == "1,234"
    assert build_docs.fmt_value(0.5, "+.2f") == "+0.50" and build_docs.fmt_value(3, "str") == "3"
    assert build_docs.lookup({"a": [{"b": 5}]}, "a.0.b") == 5


def test_prose_claims_still_hold():
    """Sentences in the templates that interpret the numbers; if a re-run changes the story, this fails."""
    m, p, d, c = RES["model.json"]["cutoffs"], RES["pipeline.json"], RES["drills.json"], RES["clustering.json"]
    assert p["checks_all_runs"]["failed_error"] == 0 and p["asof_equivalence"]["identical"]
    assert all(r["passed"] for r in p["reconciliation"]) and p["quarantine"]["rows"] == 0
    assert 0.18 < p["clicks"]["rows_folded_into_daily_totals"] / p["clicks"]["source_rows"] < 0.23      # "about a fifth"
    assert p["volume"]["weeks_flagged"] == [] and p["runs"]["by_status"] == {"SUCCESS_WITH_WARNINGS": p["runs"]["n"]}
    assert d["n_detected"] == d["n_fault_drills"] and d["n_recovered_to_clean_state"] == d["n_drills"]
    assert d["drills"][7]["drill"] == "late_data"
    for cut in ("14", "28"):       # early: boosting clearly ahead of every baseline
        assert m[cut]["gradient_boosting_vs_best_baseline"]["pr_auc"]["ci_low"] > 0.02
    assert m["28"]["best_baseline"] == m["56"]["best_baseline"] == "logistic_regression"
    assert m["28"]["models"]["rule_previous_score"]["pr_auc"] > m["28"]["models"]["rule_recent_clicks_ranking"]["pr_auc"]
    assert m["56"]["gradient_boosting_vs_best_baseline"]["pr_auc"]["difference"] < 0.02
    assert m["56"]["gradient_boosting_vs_best_baseline"]["recall_top20"]["ci_low"] < 0     # no gain shown
    assert m["84"]["gradient_boosting_vs_best_baseline"]["recall_top20"]["difference"] < 0
    for r in m.values():
        assert abs(r["ablation_with_protected_attributes"]["vs_main_model_pr_auc"]["difference"]) <=             RES["model.json"]["ablation_max_abs_pr_auc_difference"] < 0.02
        assert r["split"]["students_on_both_sides_after_split"] == 0
        assert not r["by_module"]["CCC"]["in_training_data"]
    lr = m["28"]["models"]["logistic_regression"]["top20"]
    assert 0.70 < lr["precision"] < 0.80 and lr["recall"] < 0.5        # "one flagged student in four was fine"
    assert m["14"]["models"]["logistic_regression"]["top20"]["precision"] < lr["precision"]
    f = m["28"]["fairness_gradient_boosting"]
    assert max(f, key=lambda k: f[k]["recall_gap"]) == "age_band"
    canary = RES["model.json"]["canary_shuffled_labels"]
    assert abs(canary["pr_auc_shuffled_labels_mean"] - canary["prevalence"]) < 0.03
    assert c["chosen"]["stable"] and 2 <= c["chosen"]["k"] <= 4
    unstable = [s["k"] for s in c["selection"] if s["k"] > c["chosen"]["k"] and s["min_ari"] < 0.8]
    assert len(unstable) == len([s for s in c["selection"] if s["k"] > c["chosen"]["k"]])
    assert RES["descriptive.json"]["associations_day_28"][0]["comparison"].startswith("no clicks in the two weeks")
    assert len(__import__("oulad.analytics.model", fromlist=["x"]).FEATURES) == 24
    dg, rv = RES["digest.json"], RES["digest_review.json"]
    best = max(dg["variants"], key=lambda v: v["pass_rate"])
    assert dg["variants"][4] == best and best["model"] == "qwen3.5:4b" and best["variant"] == "structured"
    assert dg["template"]["passed"] == dg["template"]["n"] == dg["cases"]
    assert rv["read_as_wrong_or_unsupported"] > rv["read_as_acceptable"]            # "most passing notes"
    assert rv["passed_verifier"] == sum(v["passed"] for v in dg["variants"] if v["model"] == "qwen3.5:4b")


def test_results_agree_with_each_other():
    p, d = RES["pipeline.json"], RES["descriptive.json"]
    assert p["students"]["registrations"] == d["outcomes"]["n"] == p["layers"]["gold.dim_student_presentation"] == 32593
    assert p["source_rows"]["studentVle"] == p["layers"]["bronze.student_vle"] == p["clicks"]["source_rows"]
    assert d["withdrawal"]["left_on_or_before_day_0"] + d["withdrawal"]["followed_from_day_0"] == d["withdrawal"]["registrations"]
    assert p["asof_equivalence"]["rows_compared"] == p["layers"]["gold.features_early_warning"] == p["layers"]["gold.features_asof"]
    manifest = json.loads((ROOT / "data" / "source_manifest.json").read_text())
    assert {k[:-4]: v["rows"] for k, v in manifest["files"].items()} == p["source_rows"]
    pb = RES["powerbi.json"]
    assert pb["measure_context_pairs"] == pb["measures"] * pb["contexts"]


def test_mutation_record_is_complete():
    m = RES["mutation.json"]
    assert m["mutants_defined"] == len(mutate.MUTANTS) == m["final"]["total"] >= 100
    assert m["first"]["total"] <= m["final"]["total"] and m["first"]["killed"] <= m["final"]["killed"]
    assert m["final"]["killed"] + m["final"]["equivalent"] == m["final"]["total"]
    for i in mutate.EQUIVALENT:
        assert len(mutate.EQUIVALENT[i]) > 30
    for rel, old, new, tests in mutate.MUTANTS:
        assert (ROOT / rel).read_text(encoding="utf-8").count(old) == 1 and old != new and tests


def test_test_count_is_recorded():
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    assert RES["tests.json"]["n_tests"] == int(re.search(r"(\d+) tests? collected", out).group(1))


def test_read_this_first_and_disclaimers():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    head = readme[:900]
    for phrase in ("Read this first", "Personal portfolio project on public data, built with AI assistance", "not\n> affiliated with",
                   "Institute of Technical Education", "Open University", "no non-public information", "Wenjie Luo (GitHub LUOaini1213)"):
        assert phrase in head, phrase
    assert "Not opened in Power BI Desktop" in readme and "No Spark was run" in readme
    assert "The data is not from Singapore" in readme
    assert (ROOT / "docs" / "fabric_mapping.md").read_text(encoding="utf-8").splitlines()[0] == "Not run on Microsoft Fabric"
    for doc in ("DATA_SOURCES.md", "docs/requirements.md", "docs/runbook.md", "docs/lineage.md", "docs/lecturer_guide.md",
                "docs/model_explainer.md"):
        assert "not affiliated with" in (ROOT / doc).read_text(encoding="utf-8")[:400], doc
    assert "ASSUMPTION" in (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    lic = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in lic and "Copyright (c) 2026 Wenjie Luo (LUOaini1213)" in lic


def test_provenance_labels_and_sources():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    table = readme[readme.index("## Provenance"):readme.index("## Reproduce")]
    rows = [r for r in table.splitlines() if r.startswith("| ") and "---" not in r][1:]
    assert len(rows) >= 12
    for r in rows:
        assert re.search(r"\| (REAL|DERIVED|ASSUMPTION|ILLUSTRATIVE|SYNTHETIC)", r), r
    assert {"REAL", "DERIVED", "ASSUMPTION", "ILLUSTRATIVE", "SYNTHETIC"} <= set(re.findall(r"\| (REAL|DERIVED|ASSUMPTION|ILLUSTRATIVE|SYNTHETIC)", table))
    sources = (ROOT / "DATA_SOURCES.md").read_text(encoding="utf-8")
    for needle in ("CC BY 4.0", "archive.ics.uci.edu", "10.1038/sdata.2017.171", "2026-11-23", "openlineage.io", "learn.microsoft.com"):
        assert needle in sources
    fabric = (ROOT / "docs" / "fabric_mapping.md").read_text(encoding="utf-8")
    for url in re.findall(r"https://learn\.microsoft\.com/\S+", fabric):
        assert url in sources, url
    assert fabric.count("UNVERIFIED") >= 4


def test_nothing_private_or_bulky_is_tracked():
    files = tracked()
    total = sum(p.stat().st_size for p in files if p.exists())
    assert total < 30e6, f"{total / 1e6:.1f} MB tracked"
    names = [p.relative_to(ROOT).as_posix() for p in files]
    assert not [n for n in names if n.startswith(("data/raw/", "lake/", ".venv/")) or n.endswith((".zip", ".parquet", ".duckdb"))]
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}")
    path = re.compile(r"[A-Z]:[\\/]Users[\\/]|/Users/[A-Za-z]|/home/[a-z]")
    secret = re.compile(r"(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}", re.I)
    for p in files:
        if not p.exists() or p.suffix in (".png",) or p.stat().st_size > 5e6:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        rel = p.relative_to(ROOT).as_posix()
        assert not email.search(text), (rel, email.search(text).group(0))
        assert not path.search(text) or rel == "tests/test_repo.py", (rel, path.search(text).group(0))
        assert not secret.search(text), rel
        assert "id_student" not in text or not rel.startswith(("dashboard/", "powerbi/")), rel


def test_fixture_is_small_and_real_shaped():
    fx = ROOT / "data" / "fixture"
    assert sum(p.stat().st_size for p in fx.glob("*.csv")) < 3e6
    assert {p.stem for p in fx.glob("*.csv")} == set(__import__("oulad.common", fromlist=["x"]).SOURCES)


def test_ci_workflow_runs_offline_checks():
    wf = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for needle in ("pytest", "build_docs.py --check", "build_powerbi.py --check", "build_preview.py --check", "mutate.py"):
        assert needle in wf, needle
    assert "fetch_data" not in wf and "ollama" not in wf.lower()
    assert sys.version_info >= (3, 11)
