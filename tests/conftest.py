"""Session fixtures: the weekly replay of the committed sample, run once and shared by the slow tests."""
from pathlib import Path

import pytest

from oulad import pipeline, summary
from oulad.common import FIRST_WEEK, LAST_WEEK
from oulad.lake import Lake
from oulad.replay import build_landing

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "data" / "fixture"


@pytest.fixture(scope="session")
def fx(tmp_path_factory):
    """(lake, landing, run records, summary) for the full replay of data/fixture."""
    base = tmp_path_factory.mktemp("fixture_replay")
    build_landing(FIXTURE, base / "landing")
    lake = Lake(base / "lake")
    runs = pipeline.run_range(lake, base / "landing", FIRST_WEEK, LAST_WEEK)
    return lake, base / "landing", runs, summary.pipeline_summary(lake, FIXTURE)


@pytest.fixture(scope="session")
def drill_records(tmp_path_factory):
    from oulad.drills import Drills
    return Drills(FIXTURE, tmp_path_factory.mktemp("drills") / "work").run_all()
