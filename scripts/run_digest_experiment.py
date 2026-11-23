"""Experiment: can a small local language model write the lecturer digest safely?

Usage: python scripts/run_digest_experiment.py [--models qwen3.5:4b,gemma3:4b] [--offline]

For each class (module-presentation x cut-off day) and each prompt variant, a local Ollama model drafts a note
with placeholders instead of figures; the placeholders are filled from the class snapshot and the result goes
through oulad.digest.verify. Raw model outputs are cached in results/digest_cache.json, so the scoring can be
re-run (and is re-run by the tests) without a model or a network: --offline scores the cache only.
Needs an Ollama server on localhost:11434 for anything not in the cache.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oulad import digest  # noqa: E402
from pbitest.model import LoadedModel  # noqa: E402

CACHE = ROOT / "results" / "digest_cache.json"
OUT = ROOT / "results" / "digest.json"
CUTOFFS = [28, 56]
URL = "http://localhost:11434/api/generate"


def generate(model: str, prompt: str, seed: int) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False,
                       "options": {"temperature": 0.7, "seed": seed, "num_predict": 220}}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())["response"].strip()


def cases(snapshot) -> list[tuple[str, int]]:
    mps = sorted(snapshot["MpKey"].unique())
    return [(m, c) for m in mps for c in CUTOFFS]


def score(cache: dict, snapshot) -> dict:
    variants = []
    for key in sorted({k.rsplit("|", 2)[0] for k in cache}):
        model, variant = key.split("|")
        rows = {k: v for k, v in cache.items() if k.startswith(key + "|")}
        failures, passed, examples = {}, 0, []
        for k, draft in sorted(rows.items()):
            mp, cutoff = k.split("|")[2:]
            f = digest.class_facts(snapshot, mp, int(cutoff))
            problems = digest.verify(digest.fill(draft, f), f)
            passed += not problems
            for p in problems:
                failures[p] = failures.get(p, 0) + 1
            if problems and len(examples) < 2:
                examples.append({"case": f"{mp} day {cutoff}", "problems": problems, "draft": draft[:400]})
        variants.append({"model": model, "variant": variant, "n": len(rows), "passed": passed,
                         "pass_rate": passed / len(rows) if rows else 0.0, "failures": failures, "failed_examples": examples})
    template_ok = sum(1 for m, c in cases(snapshot)
                      if not digest.verify(digest.template_digest(digest.class_facts(snapshot, m, c)), digest.class_facts(snapshot, m, c)))
    return {"cases": len(cases(snapshot)), "cutoffs": CUTOFFS, "template": {"n": len(cases(snapshot)), "passed": template_ok},
            "variants": variants,
            "note": "temperature 0.7, one seeded draft per case; pass means the filled draft has no number outside the class facts, "
                    "no student identifier, no causal wording, no unfilled placeholder, and at most 120 words"}


def main() -> None:
    snapshot = LoadedModel().frames["StudentSnapshot"]
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    if "--offline" not in sys.argv:
        models = sys.argv[sys.argv.index("--models") + 1].split(",") if "--models" in sys.argv else ["qwen3.5:4b"]
        for model in models:
            for variant, prompt in digest.PROMPTS.items():
                for seed, (mp, cutoff) in enumerate(cases(snapshot)):
                    key = f"{model}|{variant}|{mp}|{cutoff}"
                    if key not in cache:
                        cache[key] = generate(model, prompt + chr(10) + f"Class: {mp}, day {cutoff} of term.", seed)
                        CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
                        print(key, "->", len(cache[key].split()), "words", flush=True)
    out = score(cache, snapshot)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    for v in out["variants"]:
        print(f"{v['model']:<12} {v['variant']:<11} {v['passed']}/{v['n']} {v['failures']}")


if __name__ == "__main__":
    main()
