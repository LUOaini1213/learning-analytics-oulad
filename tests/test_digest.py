"""The lecturer digest: the fixed template, and the verifier every generated wording has to pass."""
import pandas as pd
import pytest

from oulad import digest
from pbitest.model import LoadedModel

SNAP = pd.DataFrame({
    "MpKey": ["BBB-2014J"] * 5 + ["AAA-2014J"], "CutoffDay": [28] * 6, "SpKey": ["0a1b2c3d4e5f", "111111111111", "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc", "dddddddddddd"],
    "Flagged": [1, 0, 0, 0, 1, 0], "ClicksLast2w": [0, 12, 0, 40, 0, 3], "AssessmentsMissed": [1, 0, 0, 0, 2, 0],
    "AvgScore": [41.0, 80.0, None, 71.5, None, 60.0], "DaysSinceLastActive": [20, 1, 16, 0, 30, 2]})
F = digest.class_facts(SNAP, "BBB-2014J", 28)


def test_class_facts_by_hand():
    assert F == {"module": "BBB-2014J", "cutoff_day": 28, "students": 5, "flagged": 2, "inactive": 3, "missed": 2,
                 "average_score": 64.2, "median_days_since_active": 16.0}
    with pytest.raises(ValueError):
        digest.class_facts(SNAP, "BBB-2014J", 14)
    none = digest.class_facts(SNAP.assign(AvgScore=None), "AAA-2014J", 28)
    assert none["average_score"] is None and "average assessment score" not in digest.template_digest(none)


def test_template_passes_its_own_verifier_and_states_the_facts():
    text = digest.template_digest(F)
    assert digest.verify(text, F) == []
    for needle in ("5 students are registered", "3 had no clicks", "2 have an assessment", "2 students are flagged", "64.2"):
        assert needle in text
    assert digest.template_digest(F) == text


def test_template_passes_for_every_class_in_the_dashboard_extract():
    snap = LoadedModel().frames["StudentSnapshot"]
    cases = snap[["MpKey", "CutoffDay"]].drop_duplicates()
    assert len(cases) == 28
    for r in cases.itertuples():
        f = digest.class_facts(snap, r.MpKey, int(r.CutoffDay))
        assert digest.verify(digest.template_digest(f), f) == [], (r.MpKey, r.CutoffDay)


@pytest.mark.parametrize("text, problem", [
    ("Your class has 5 students and 4 are flagged.", "number_not_in_source"),
    ("About 60% of the class is inactive.", "number_not_in_source"),
    ("Three students are inactive and seven are flagged.", "number_not_in_source"),
    ("5 students; contact 0a1b2c3d4e5f first.", "student_identifier"),
    ("5 students; student id 4821 needs a call.", "student_identifier"),
    ("3 students are inactive because the assignment was hard.", "causal_claim"),
    ("Low scores led to 2 flags.", "causal_claim"),
    ("Inactivity is due to holidays.", "causal_claim"),
    ("", "length"),
    ("word " * 121, "length"),
    ("There are {students} students and {enrolment} enrolled.", "unfilled_placeholder"),
])
def test_verifier_rejects(text, problem):
    assert problem in digest.verify(text, F)


def test_verifier_accepts_honest_variants():
    ok = ["BBB-2014J: 5 students are registered; 3 were not seen online in the last two weeks and 2 are flagged.",
          "Of your 5 students, 2 have missed an assessment. The average score so far is 64.2.",
          "At day 28, 3 of 5 students had no clicks in the last 2 weeks."]
    for text in ok:
        assert digest.verify(text, F) == [], text


def test_fill_replaces_known_placeholders_only():
    draft = "You have {students} students, {flagged} flagged, score {average_score}; {unknown} stays."
    assert digest.fill(draft, F) == "You have 5 students, 2 flagged, score 64.2; {unknown} stays."
    no_score = dict(F, average_score=None)
    assert "{average_score}" in digest.fill(draft, no_score)
    assert digest.verify(digest.fill("{students} students, {flagged} flagged.", F), F) == []


def test_prompts_ask_for_placeholders_and_forbid_causes():
    assert set(digest.PROMPTS) == {"zero_shot", "structured", "few_shot"}
    for name, p in digest.PROMPTS.items():
        for ph in ("{students}", "{flagged}", "{inactive}", "{missed}"):
            assert ph in p, (name, ph)
        assert "digit" in p and "0a1b" not in p


def test_cached_model_drafts_score_as_recorded():
    """The language-model experiment is re-scored offline from the cached drafts."""
    import json
    from pathlib import Path
    from scripts import run_digest_experiment as exp
    root = Path(__file__).resolve().parent.parent
    cache = json.loads((root / "results" / "digest_cache.json").read_text(encoding="utf-8"))
    recorded = json.loads((root / "results" / "digest.json").read_text(encoding="utf-8"))
    fresh = exp.score(cache, LoadedModel().frames["StudentSnapshot"])
    assert fresh == recorded
    assert len(cache) == sum(v["n"] for v in recorded["variants"]) == 84 and len(recorded["variants"]) == 6
    for v in recorded["variants"]:
        assert v["passed"] + sum(1 for _ in []) <= v["n"] and 0 < v["passed"] < v["n"]      # no variant is perfect
        assert sum(v["failures"].values()) >= v["n"] - v["passed"]
    review = json.loads((root / "results" / "digest_review.json").read_text(encoding="utf-8"))
    assert {i["case"] for i in review["items"]} <= set(cache)
    assert review["read_as_wrong_or_unsupported"] == len(review["items"])
    assert review["read_as_acceptable"] + len(review["items"]) == review["passed_verifier"]
