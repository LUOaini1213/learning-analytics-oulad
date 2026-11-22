"""Run the failure drills on the committed sample and write results/drills.json.

Usage: python scripts/run_drills.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from oulad.drills import Drills, summarise  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        out = summarise(Drills(ROOT / "data" / "fixture", Path(tmp) / "drills").run_all())
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "drills.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    for r in out["drills"]:
        print(f"{r['drill']:<18} detected={r['detected']!s:<5} status={r['status']:<22} "
              f"recovered={r['matches_clean_run_after_remediation']}  by={r['detected_by']}")


if __name__ == "__main__":
    main()
