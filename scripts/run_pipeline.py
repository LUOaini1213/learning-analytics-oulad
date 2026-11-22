"""Replay the extract week by week through the medallion pipeline and write results/pipeline.json.

Usage: python scripts/run_pipeline.py [--raw data/raw] [--lake lake/full] [--fixture] [--keep]
  --fixture   run on the committed sample in data/fixture (what the tests and CI do)
  --keep      reuse an existing landing area and lake instead of starting again
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oulad import pipeline, summary  # noqa: E402
from oulad.common import FIRST_WEEK, LAST_WEEK  # noqa: E402
from oulad.lake import Lake  # noqa: E402
from oulad.replay import build_landing  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--lake", default=None)
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    raw = ROOT / "data" / "fixture" if args.fixture else Path(args.raw)
    base = Path(args.lake) if args.lake else ROOT / "lake" / ("fixture" if args.fixture else "full")
    landing, lake = base / "landing", Lake(base / "lake")
    t0 = time.perf_counter()
    if not args.keep or not landing.exists():
        if base.exists():
            shutil.rmtree(base)
        print("cutting the extract into weekly deliveries ...", flush=True)
        build_landing(raw, landing)
    done = {r["week"] for r in pipeline.read_run_log(lake) if r["status"].startswith("SUCCESS")}
    first = max(done) + 1 if done else FIRST_WEEK
    pipeline.run_range(lake, landing, first, LAST_WEEK, verbose=True)
    out = summary.pipeline_summary(lake, raw)
    out["wall_clock_seconds_this_invocation"] = round(time.perf_counter() - t0, 1)
    if not args.fixture:
        (ROOT / "results").mkdir(exist_ok=True)
        (ROOT / "results" / "pipeline.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("runs", "layers", "reconciliation", "asof_equivalence")}, indent=1)[:3000])


if __name__ == "__main__":
    main()
