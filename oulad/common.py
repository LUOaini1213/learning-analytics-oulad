"""Shared constants: source tables, week arithmetic and repository paths."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL = ROOT / "sql"
CONTRACTS = ROOT / "contracts"
RESULTS = ROOT / "results"

# source file -> (bronze table name, columns exactly as published)
SOURCES = {
    "courses": ("courses", ["code_module", "code_presentation", "module_presentation_length"]),
    "assessments": ("assessments", ["code_module", "code_presentation", "id_assessment", "assessment_type", "date",
                                    "weight"]),
    "vle": ("vle", ["id_site", "code_module", "code_presentation", "activity_type", "week_from", "week_to"]),
    "studentInfo": ("student_info", ["code_module", "code_presentation", "id_student", "gender", "region",
                                     "highest_education", "imd_band", "age_band", "num_of_prev_attempts",
                                     "studied_credits", "disability", "final_result"]),
    "studentRegistration": ("student_registration", ["code_module", "code_presentation", "id_student",
                                                     "date_registration", "date_unregistration"]),
    "studentAssessment": ("student_assessment", ["id_assessment", "id_student", "date_submitted", "is_banked",
                                                 "score"]),
    "studentVle": ("student_vle", ["code_module", "code_presentation", "id_student", "id_site", "date",
                                   "sum_click"]),
}
META_COLUMNS = ["_batch_week", "_source_file", "_file_sha256", "_source_row", "_run_id", "_ingested_at"]

FIRST_WEEK = -5   # the opening batch: reference tables and everything dated before day -28
LAST_WEEK = 39    # the closing batch: final results and anything dated after the last teaching week
PSEUDONYM_SALT_ENV = "OULAD_PSEUDONYM_SALT"
DEFAULT_SALT = "public-demo-salt-not-a-secret"


def week_of(day: int) -> int:
    """Week index of a day relative to the presentation start: days 0-6 are week 0, days -7..-1 are week -1."""
    return math.floor(day / 7)


def as_of_day(week: int) -> int:
    """Last day covered by the batch for a week."""
    return 7 * week + 6


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
