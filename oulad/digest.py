"""Weekly digest for a lecturer: a few sentences about one class at one cut-off.

The default is a fixed template (`template_digest`): deterministic, and every number comes from the class
snapshot. `verify` is the gate any generated wording has to pass, whether it comes from the template or from
a language model: no number that is not in the source facts, no student identifier, no causal claim, and a
sensible length. Language-model drafts are written with placeholders such as {flagged} instead of digits and
filled in afterwards (`fill`), so the model never types a figure itself.
"""
from __future__ import annotations

import re

import pandas as pd

FIELDS = ["students", "flagged", "inactive", "missed", "average_score", "median_days_since_active", "cutoff_day"]
CAUSAL = ["because", "due to", "caused", "causes", "cause of", "leads to", "led to", "as a result", "result of",
          "results in", "owing to", "thanks to", "therefore", "explains why", "the reason"]
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
ALWAYS_ALLOWED = {2.0}          # "the last two weeks" is part of the definition of inactive
MAX_WORDS = 120
NUMBER = re.compile(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)*%?")
KEY = re.compile(r"\b[0-9a-f]{12}\b|\b(?:student|learner)\s*(?:id|key|number|no\.?|#)?\s*[:#]?\s*[0-9a-f]*\d[0-9a-f]{3,}\b", re.I)


def class_facts(snapshot: pd.DataFrame, mp_key: str, cutoff_day: int) -> dict:
    """The numbers a digest may use, from the StudentSnapshot extract."""
    d = snapshot[(snapshot["MpKey"] == mp_key) & (snapshot["CutoffDay"] == cutoff_day)]
    if d.empty:
        raise ValueError(f"no snapshot for {mp_key} at day {cutoff_day}")
    score = d["AvgScore"].dropna()
    return {"module": mp_key, "cutoff_day": int(cutoff_day), "students": int(len(d)), "flagged": int((d["Flagged"] == 1).sum()),
            "inactive": int((d["ClicksLast2w"] == 0).sum()), "missed": int((d["AssessmentsMissed"] > 0).sum()),
            "average_score": None if score.empty else round(float(score.mean()), 1),
            "median_days_since_active": round(float(d["DaysSinceLastActive"].astype(float).median()), 1)}


def template_digest(f: dict) -> str:
    parts = [f"{f['module']}, day {f['cutoff_day']}: {f['students']} students are registered.",
             f"{f['inactive']} had no clicks in the last two weeks and {f['missed']} have an assessment that was due and not submitted.",
             f"{f['flagged']} students are flagged for follow-up; the list on the dashboard gives the reasons for each."]
    if f["average_score"] is not None:
        parts.append(f"The average assessment score so far is {f['average_score']}.")
    parts.append("These figures describe activity and submitted work only; they do not say why a student is behind.")
    return " ".join(parts)


def fill(draft: str, f: dict) -> str:
    """Replace {placeholders} with the facts. An unknown placeholder is left in place and fails verification."""
    def repl(m):
        key = m.group(1)
        if key in f and f[key] is not None:
            return str(f[key])
        return m.group(0)
    return re.sub(r"\{(\w+)\}", repl, draft)


def allowed_numbers(f: dict) -> set[float]:
    out = set(ALWAYS_ALLOWED)
    for k in FIELDS:
        if f.get(k) is not None:
            out.add(float(f[k]))
    out |= {float(x) for x in re.findall(r"\d+", f["module"])}      # the year inside a code such as BBB-2014J
    return out


def verify(text: str, f: dict) -> list[str]:
    """Reasons the text must not be sent; an empty list means it passes."""
    problems = []
    words = text.split()
    if not words or len(words) > MAX_WORDS:
        problems.append("length")
    if re.search(r"\{\w*\}", text):
        problems.append("unfilled_placeholder")
    body = text.replace(f["module"], " ")
    allowed = allowed_numbers(f)
    stated = [float(n.rstrip("%").replace(",", "")) for n in NUMBER.findall(body)]
    stated += [float(v) for w, v in WORDS.items() if re.search(rf"\b{w}\b", body, re.I)]
    if any(n not in allowed for n in stated):
        problems.append("number_not_in_source")
    if KEY.search(body):
        problems.append("student_identifier")
    low = " " + re.sub(r"\s+", " ", text.lower()) + " "
    if any(re.search(rf"\b{re.escape(c)}\b", low) for c in CAUSAL):
        problems.append("causal_claim")
    return problems


PROMPTS = {
    "zero_shot": ("Write a short weekly note (at most 80 words) for a lecturer about their class. Use these placeholders exactly "
                  "where a figure is needed and never write a digit yourself: {students} students registered, {flagged} flagged "
                  "for follow-up, {inactive} with no clicks in the last two weeks, {missed} with a missed assessment, "
                  "{average_score} average score. Do not explain or guess why anything happened. Do not name any student."),
    "structured": ("You write weekly notes for lecturers.\nRULES\n1. Three sentences, at most 80 words.\n2. Figures appear only as these "
                   "placeholders, copied exactly: {students}, {flagged}, {inactive}, {missed}, {average_score}. Never type a digit.\n"
                   "3. State facts only. No causes, no reasons, no words such as because, due to, therefore, as a result.\n"
                   "4. No student names, codes or identifiers.\n5. End by pointing the lecturer to the dashboard list.\n"
                   "MEANING OF THE PLACEHOLDERS\n{students}: students registered. {flagged}: students flagged for follow-up. "
                   "{inactive}: students with no clicks in the last two weeks. {missed}: students with an assessment due and not "
                   "submitted. {average_score}: average assessment score so far.\nWrite the note now."),
    "few_shot": ("Write a weekly note for a lecturer, in the style of the examples. Figures appear only as placeholders in curly "
                 "brackets, copied exactly; never type a digit. No causes, no student identifiers. At most 80 words.\n\n"
                 "Example 1:\nYour class has {students} registered students. {inactive} of them had no clicks in the last two weeks, "
                 "and {missed} have an assessment that was due and not submitted. {flagged} students are flagged for follow-up; "
                 "please see the dashboard list.\n\n"
                 "Example 2:\nThis week {flagged} of your {students} students are flagged for follow-up. {inactive} students were not "
                 "seen online in the last two weeks and {missed} have missed an assessment. The average score so far is "
                 "{average_score}. The dashboard list shows who to contact first.\n\nNow write a new note in your own words."),
}
