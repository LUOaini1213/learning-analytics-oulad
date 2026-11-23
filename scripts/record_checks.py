"""Record the test count and the Power BI check counts in results/ (both are quoted in the README).

Usage: python scripts/record_checks.py
The Tabular Object Model and JSON-schema validations of the Power BI project were run by hand on the date
below with tools that are not part of this repository; their outcome is recorded here as constants.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MANUAL = {"checked_on": "2026-11-23", "tom_version": "19.117.0", "tom_validation_errors": 0, "tom_tables": 12,
          "tom_relationships": 11, "tom_roles": 3, "pbir_files_validated": 27, "pbir_schema_errors": 0,
          "pbir_schema_commit": "49f4a76", "opened_in_power_bi_desktop": False}


def main() -> None:
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"], cwd=ROOT,
                         capture_output=True, text=True).stdout
    n = int(re.search(r"(\d+) tests? collected", out).group(1))
    (ROOT / "results" / "tests.json").write_text(json.dumps({"n_tests": n}, indent=1), encoding="utf-8")
    from pbitest.model import LoadedModel
    from tests.test_powerbi import CONTEXTS
    m = LoadedModel()
    pb = {"measures": len(m.measures), "contexts": len(CONTEXTS), "measure_context_pairs": len(m.measures) * len(CONTEXTS),
          "tables": len(m.meta.tables), "roles": len(m.roles), **MANUAL}
    (ROOT / "results" / "powerbi.json").write_text(json.dumps(pb, indent=1), encoding="utf-8")
    print(n, "tests;", pb["measure_context_pairs"], "measure-context pairs")


if __name__ == "__main__":
    main()
