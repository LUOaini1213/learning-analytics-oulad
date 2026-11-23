"""Plant bugs in the core logic one at a time and check that the test suite notices each one.

Usage: python scripts/mutate.py --record first|final|ci [--require-all] [--only N,N]

Each mutant is one text replacement in one file. The tests named for the mutant run first; if they pass,
the rest of the fast suite runs, so a mutant survives only if no fast test fails. The slow replay tests and
the golden tests (generated files match their generator, documents match results) are excluded, so a kill
has to come from a behavioural test. Every file is restored after its mutant has run. Results go to
results/mutation.json under the given key. Mutants judged equivalent are listed in EQUIVALENT with a reason.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "mutation.json"
PYTEST = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-m", "not slow and not golden",
          "--deselect", "tests/test_repo.py::test_mutation_record_is_complete"]
SG, RB, FE, CA, AN, PB = ("tests/test_silver_gold.py", "tests/test_replay_bronze.py", "tests/test_features.py",
                          "tests/test_contracts_access.py", "tests/test_analytics.py", "tests/test_powerbi.py")
MO = "tests/test_more.py"
ALL = [SG, FE, AN, PB, MO, "tests/test_digest.py", CA, RB, "tests/test_fixture_pipeline.py", "tests/test_repo.py"]
SIL, GOLD, FEAT, ACC = "sql/silver/", "sql/gold/", "sql/features/features_early_warning.sql", "sql/access/20_management.sql"
MET, MOD, CLU = "oulad/analytics/metrics.py", "oulad/analytics/model.py", "oulad/analytics/clustering.py"
TM = "powerbi/LearningAnalytics.SemanticModel/definition/tables/"

MUTANTS = [
    # --- silver: typing, rejection rules, de-duplication, incremental recompute
    (SIL + "vle_daily.sql", "cast(sum(sum_click) as bigint) as sum_click", "cast(max(sum_click) as bigint) as sum_click", [SG]),
    (SIL + "vle_daily.sql", "min(_batch_week) as first_batch_week", "max(_batch_week) as first_batch_week", [SG]),
    (SIL + "vle_daily.sql", "  and event_week in (select week from run.affected_weeks)\n", "", [SG]),
    (SIL + "stage_student_vle.sql", "< 1 then 'click_count_out_of_range'", "< 0 then 'click_count_out_of_range'", [SG]),
    (SIL + "stage_student_vle.sql", "not between -60 and 400 then 'date_out_of_range'", "not between -60 and 4000 then 'date_out_of_range'", [SG]),
    (SIL + "stage_student_vle.sql", "when r.id_student is null then 'orphan_student'", "when false then 'orphan_student'", [SG]),
    (SIL + "stage_student_vle.sql", "floor(try_cast(nullif(trim(v.date), '?') as integer) / 7.0)", "ceil(try_cast(nullif(trim(v.date), '?') as integer) / 7.0)", [SG]),
    (SIL + "student_registration.sql", "order by _batch_week desc, _source_row desc) as _rk", "order by _batch_week asc, _source_row desc) as _rk", [SG]),
    (SIL + "assessments.sql", "coalesce(s.due_day, c.module_presentation_length) as due_day_effective", "coalesce(s.due_day, 0) as due_day_effective", [SG, FE]),
    (SIL + "stage_student_info.sql", "case when trim(s.imd_band) = '10-20' then '10-20%' else", "case when false then '10-20%' else", [SG]),
    (SIL + "stage_student_assessment.sql", "not between 0 and 100 then 'score_out_of_range'", "not between 0 and 1000 then 'score_out_of_range'", [SG]),
    (SIL + "stage_assessments.sql", "when c.code_module is null then 'orphan_module_presentation'", "when false then 'orphan_module_presentation'", [SG]),
    ("oulad/transform.py", '        affected |= _weeks(con, "select distinct event_week from silver.quarantine where source_table = \'student_vle\'")\n    steps.materialise(lake, con, "silver.quarantine"',
     '        pass\n    steps.materialise(lake, con, "silver.quarantine"', [SG]),
    ("oulad/transform.py", "predicate = f\"event_week in ({', '.join(map(str, weeks))})\" if weeks else None", "predicate = None", [SG]),
    # --- gold
    (GOLD + "fact_student_week.sql", "count(distinct date) as active_days", "count(date) as active_days", [MO, SG]),
    (GOLD + "fact_student_week.sql", "max(date) as last_active_day", "min(date) as last_active_day", [SG]),
    (GOLD + "fact_assessment.sql", "case when s.is_banked = 0 then s.date_submitted - a.due_day_effective end", "s.date_submitted - a.due_day_effective", [SG]),
    (GOLD + "fact_assessment.sql", "s.date_submitted - a.due_day_effective end", "a.due_day_effective - s.date_submitted end", [SG]),
    (GOLD + "dim_student_presentation.sql", "when i.final_result in ('Withdrawn', 'Fail') then 1", "when i.final_result in ('Withdrawn') then 1", [SG]),
    (GOLD + "dim_student_presentation.sql", "'|student|', cast(r.id_student as varchar)", "'|student|', r.code_presentation, cast(r.id_student as varchar)", [SG, FE]),
    (GOLD + "dim_student_presentation.sql", "concat((select pseudonym_salt from run.settings), '|', r.code_module", "concat('', '|', r.code_module", [SG]),
    (GOLD + "dim_activity_type.sql", "when activity_type = 'homepage' then 'Homepage'", "when activity_type = 'homepage' then 'Content'", [SG]),
    # --- as-of features
    (FEAT, "and d.date_registration < c.cutoff_day", "and d.date_registration <= c.cutoff_day", [FE]),
    (FEAT, "d.date_unregistration >= c.cutoff_day)", "d.date_unregistration > c.cutoff_day)", [FE]),
    (FEAT, "    where d.date_registration is not null\n", "    where true\n", [FE]),
    (FEAT, "join gold.fact_student_week f on f.sp_key = p.sp_key and f.week_index < p.cutoff_day / 7", "join gold.fact_student_week f on f.sp_key = p.sp_key and f.week_index <= p.cutoff_day / 7", [FE]),
    (FEAT, "join gold.fact_engagement_weekly f on f.sp_key = p.sp_key and f.week_index < p.cutoff_day / 7", "join gold.fact_engagement_weekly f on f.sp_key = p.sp_key", [FE]),
    (FEAT, "join gold.fact_assessment f on f.sp_key = p.sp_key and f.date_submitted < p.cutoff_day", "join gold.fact_assessment f on f.sp_key = p.sp_key", [FE]),
    (FEAT, "where a.assessment_type <> 'Exam' and a.due_day_effective < p.cutoff_day", "where a.assessment_type <> 'Exam' and a.due_day_effective <= p.cutoff_day + 6", [FE]),
    (FEAT, "    where a.assessment_type <> 'Exam' and a.due_day_effective < p.cutoff_day", "    where a.due_day < p.cutoff_day or a.due_day is null", [FE]),
    (FEAT, "sum(case when f.week_index >= p.cutoff_day / 7 - 2 then f.clicks else 0 end) as clicks_last_2w", "sum(case when f.week_index >= p.cutoff_day / 7 - 1 then f.clicks else 0 end) as clicks_last_2w", [FE]),
    (FEAT, "else p.cutoff_day - 1 - e.last_active_day end", "else p.cutoff_day - e.last_active_day end", [FE]),
    (FEAT, "sum(case when f.is_banked = 0 then 1 else 0 end) as n_submitted", "count(*) as n_submitted", [FE]),
    (FEAT, "coalesce(d.n_due, 0) - coalesce(s.n_submitted_due, 0) as n_missed", "coalesce(d.n_due, 0) - coalesce(s.n_submitted, 0) as n_missed", [FE]),
    (FEAT, "sum(case when f.days_late > 0 then 1 else 0 end) as n_late", "sum(case when f.days_late >= 0 then 1 else 0 end) as n_late", [MO, FE]),
    (FEAT, "over (partition by j.cutoff_day, j.mp_key)", "over (partition by j.mp_key)", [FE]),
    ("oulad/leakage.py", "    perturb_future(altered, cutoff_day)\n", "", [FE]),
    ("oulad/leakage.py", 'if list(before["sp_key"]) != list(after["sp_key"]):', "if False:", [FE]),
    # --- suppression and access
    (ACC, "(f.primary_n or (f.n_primary = 1 and f.rank_among_shown = 1)) as hide_n", "f.primary_n as hide_n", [CA]),
    (ACC, "           a.n_students < 5 as primary_n,", "           a.n_students < 4 as primary_n,", [CA]),
    (ACC, "and d.n_at_risk >= 5\n", "and d.n_at_risk >= 1\n", [CA]),
    (ACC, "case when b.n_students >= 5 then b.n_students end as n_students", "b.n_students as n_students", [CA]),
    (ACC, "       case when b.n_active_students >= 5 then b.clicks end as clicks", "       b.clicks as clicks", [CA]),
    ("sql/access/00_roles.sql", "where a.app_user = s.app_user", "where true", [CA]),
    ("sql/access/10_lecturer.sql", "select d.sp_key, d.mp_key, d.num_of_prev_attempts", "select d.sp_key, d.mp_key, d.gender, d.num_of_prev_attempts", [CA]),
    ("sql/access/20_management.sql", "where s.app_role in ('management', 'data_engineer');\n\ncreate or replace view access.mgmt_outcomes_by_group",
     "where true;\n\ncreate or replace view access.mgmt_outcomes_by_group", [CA]),
    ("oulad/access.py", "    if len(hidden) == 1:", "    if len(hidden) == 99:", [CA]),
    ("oulad/access.py", "hidden = {k for k, v in counts.items() if v < threshold}", "hidden = {k for k, v in counts.items() if v <= threshold}", [CA]),
    ("oulad/access.py", "return k if (n >= threshold and k >= threshold and n - k >= threshold) else None", "return k if (n >= threshold and k >= threshold) else None", [CA]),
    # --- contracts
    ("oulad/contracts.py", "having count(*) > 1) d", "having count(*) > 2) d", [CA]),
    ("oulad/contracts.py", 'conds = ([f"{col} < {rule[\'min\']}"]', 'conds = ([f"{col} < {rule[\'min\']} - 1"]', [CA]),
    ("oulad/contracts.py", "left == right, left, right", "True, left, right", [CA]),
    ("oulad/contracts.py", "    return None if med == 0 else current / med", "    return None if med == 0 else med / max(current, 1)", [CA]),
    ("oulad/contracts.py", "for w in range(week - rule[\"window_weeks\"], week)]", "for w in range(0, week)]", [CA]),
    ("oulad/contracts.py", 'ok = ratio is not None and rule["min_ratio"] <= ratio <= rule["max_ratio"]', 'ok = ratio is None or rule["min_ratio"] <= ratio <= rule["max_ratio"]', [MO, CA]),
    # --- ingest gate, replay, run controller
    ("oulad/bronze.py", '        if sha != manifest[name]["sha256"]:', "        if False:", [RB]),
    ("oulad/bronze.py", "        lost = [c for c in expected if c not in header]", "        lost = []", [RB]),
    ("oulad/bronze.py", 'if dup and (dup["source"], dup["batch_week"]) != (name, week) and manifest[name]["rows"] > 0:', "if False:", [RB]),
    ("oulad/bronze.py", "missing = [w for w in range(FIRST_WEEK, week) if w not in loaded_weeks]", "missing = []", [RB]),
    ("oulad/bronze.py", 'if prev and prev["file_sha256"] == files[name]["sha256"]:', "if False:", [RB]),
    ("oulad/bronze.py", '            if t.num_rows != info["rows"]:', "            if False:", [RB]),
    ("oulad/bronze.py", 'partition_by=["_batch_week"], predicate=f"_batch_week = {week}")\n        loaded[name] = t.num_rows',
     'partition_by=["_batch_week"], mode="append")\n        loaded[name] = t.num_rows', [RB]),
    ("oulad/replay.py", "case when {unreg_w} <= {reg_w} then date_unregistration else '?' end as date_unregistration", "date_unregistration", [RB]),
    ("oulad/replay.py", "select {info}, '?' as final_result, {reg_w} as arrival_week, i._rn", "select {info}, i.final_result, {reg_w} as arrival_week, i._rn", [RB]),
    ("oulad/replay.py", 'WEEK = "cast(floor({e} / 7.0) as integer)"', 'WEEK = "cast(round({e} / 7.0) as integer)"', [RB]),
    ("oulad/pipeline.py", '    return sorted(w for w, status in last.items() if w < week and status == "ROLLED_BACK")', "    return []", [RB]),
    ("oulad/pipeline.py", "    if errors:\n        restored = _restore(lake, before)", "    if errors:\n        restored = []", [RB]),
    ("oulad/pipeline.py", '"passed": share <= QUARANTINE_ERROR_SHARE, "observed"', '"passed": True, "observed"', [RB]),
    ("oulad/pipeline.py", "    if 7 * (week + 1) in CUTOFFS:", "    if 7 * week in CUTOFFS:", [RB]),
    ("oulad/common.py", "    return math.floor(day / 7)", "    return int(day / 7)", [SG]),
    # --- metrics
    (MET, "    last_of_tie = np.r_[score[1:] != score[:-1], True]", "    last_of_tie = np.ones(len(score), dtype=bool)", [AN]),
    (MET, "    return max(1, math.ceil(fraction * n - 1e-9))", "    return max(1, math.floor(fraction * n))", [AN]),
    (MET, '    return np.argsort(-np.asarray(score, dtype=float), kind="stable")', '    return np.argsort(np.asarray(score, dtype=float), kind="stable")', [AN]),
    (MET, "    recall = tp / y.sum() if y.sum() else float(\"nan\")", "    recall = tp / len(y) if y.sum() else float(\"nan\")", [AN]),
    (MET, "    precision = tp / flag.sum() if flag.sum() else float(\"nan\")", "    precision = tp / y.sum() if flag.sum() else float(\"nan\")", [AN]),
    (MET, "    return float(np.mean((prob - y) ** 2))", "    return float(np.mean(np.abs(prob - y)))", [AN]),
    (MET, "        at_risk = int(np.sum(time >= t))", "        at_risk = int(np.sum(time > t))", [AN]),
    (MET, "        s *= 1 - d / at_risk", "        s *= 1 - d / len(time)", [AN]),
    (MET, "        if row[\"day\"] <= day:", "        if row[\"day\"] < day:", [AN]),
    (MET, "        diffs.append(metric(y[i], a[i]) - metric(y[i], b[i]))", "        diffs.append(metric(y[i], a[i]) - metric(y, b))", [MO, AN]),
    (MET, "        idx = np.flatnonzero(groups == g)\n        flag[idx[flag_top(score[idx], fraction)]] = True", "        idx = np.flatnonzero(groups == g)\n        flag[idx[flag_top(score, fraction)[idx]]] = True", [AN]),
    (MET, "    which = np.minimum((prob * bins).astype(int), bins - 1)", "    which = np.minimum((prob * bins).astype(int), bins)", [AN]),
    (MET, "    return float(y[exposed].mean() / y[~exposed].mean())", "    return float(y[~exposed].mean() / y[exposed].mean())", [AN]),
    # --- model
    (MOD, "    train = train_all[~train_all[\"student_key\"].isin(overlap)]", "    train = train_all", [AN]),
    (MOD, '    test = df[df["presentation_year"] == 2014]', '    test = df[df["presentation_year"] >= 2013]', [AN]),
    (MOD, '    return np.where(np.isnan(score), np.where(due, 100.0, 50.0), 100.0 - score)', '    return np.where(np.isnan(score), np.where(due, 100.0, 50.0), score)', [AN]),
    (MOD, '    return -df["clicks_last_2w"].to_numpy(dtype=float)', '    return df["clicks_last_2w"].to_numpy(dtype=float)', [AN]),
    (MOD, "            names = [c for c in row.sort_values(ascending=False).index if row[c] > 0][:top]", "            names = [c for c in row.sort_values(ascending=True).index][:top]", [MO, AN]),
    (MOD, '    return "Most deprived 30%" if low < 30 else "Middle 40%" if low < 70 else "Least deprived 30%"', '    return "Most deprived 30%" if low <= 30 else "Middle 40%" if low < 70 else "Least deprived 30%"', [AN]),
    (MOD, "        gb = boosted(x_tr, rng.permutation(train[\"at_risk\"].to_numpy()))", "        gb = boosted(x_tr, train[\"at_risk\"].to_numpy())", [AN]),
    (CLU, '    stable = [r for r in table if r["mean_ari"] >= STABLE_ARI and r["min_ari"] >= STABLE_ARI]', '    stable = [r for r in table if r["mean_ari"] >= STABLE_ARI]', [AN]),
    (CLU, "        aris.append(adjusted_rand_score(ref, fit(x[idx], k, seed + 1 + b).predict(x)))", "        aris.append(adjusted_rand_score(ref, fit(x, k, seed).predict(x)))", [AN]),
    # --- DAX measures (as written in the TMDL files) and the security roles
    (TM + "StudentSnapshot.tmdl", "measure 'Flagged Share' = DIVIDE([Flagged Students], [Students])", "measure 'Flagged Share' = DIVIDE([Students], [Flagged Students])", [PB]),
    (TM + "StudentSnapshot.tmdl", "StudentSnapshot[ClicksLast2w] = 0)", "StudentSnapshot[ClicksLast2w] <= 1)", [PB]),
    (TM + "StudentSnapshot.tmdl", "StudentSnapshot[AssessmentsMissed] > 0)", "StudentSnapshot[AssessmentsMissed] >= 0)", [PB]),
    (TM + "StudentSnapshot.tmdl", "PERCENTILE.INC(StudentSnapshot[DaysSinceLastActive], 0.5)", "PERCENTILE.INC(StudentSnapshot[DaysSinceLastActive], 0.6)", [PB]),
    (TM + "AggModuleWeek.tmdl", "measure 'Active Share' = DIVIDE([Active Students], [Enrolled Students])", "measure 'Active Share' = DIVIDE([Active Students], [Clicks])", [PB]),
    (TM + "AggOutcome.tmdl", "DIVIDE([Withdrawn] + [Failed], [Registrations])", "DIVIDE([Withdrawn], [Registrations] - [Failed])", [PB]),
    (TM + "AggOutcomeByGroup.tmdl", "SUM(AggOutcomeByGroup[StudentsWithRate]))", "SUM(AggOutcomeByGroup[Students]))", [PB]),
    (TM + "AggWithdrawal.tmdl", "RETURN IF(ISBLANK(gone), BLANK(), 1 - gone)", "RETURN IF(ISBLANK(gone), BLANK(), gone)", [PB]),
    (TM + "AggActivityWeek.tmdl", 'CALCULATE([Activity Clicks], AggActivityWeek[ActivityGroup] <> "")', "[Activity Clicks]", [PB]),
    (TM + "OpsRunLog.tmdl", 'OpsRunLog[Status] IN {"REJECTED_AT_INGEST", "ROLLED_BACK"}', 'OpsRunLog[Status] IN {"REJECTED_AT_INGEST"}', [PB]),
    (TM + "OpsRunLog.tmdl", "MAX(OpsRunLog[QuarantineRows])", "SUM(OpsRunLog[QuarantineRows])", [PB]),
    (TM + "OpsChecks.tmdl", "DIVIDE([Checks Run] - [Checks Failed], [Checks Run])", "DIVIDE([Checks Failed], [Checks Run])", [PB]),
    ("powerbi/LearningAnalytics.SemanticModel/definition/roles/CollegeManagement.tmdl", "tablePermission StudentSnapshot = FALSE()\n\n", "", [PB]),
    ("pbitest/model.py", "            return df[m.group(7)].isin(allowed)", "            return df[m.group(7)].notna()", [PB]),
    ("pbitest/model.py", "                if r.to_table in limited:", "                if False:", [PB]),
    ("scripts/export_dashboard.py", "case when n_left >= {T} and n - n_left >= {T} then n_left end as LeftByWeekEnd", "n_left as LeftByWeekEnd", [MO, PB]),
]

# mutants that cannot change any output, with the reason
EQUIVALENT: dict[int, str] = {
    24: "Dropping 'date_registration is not null' changes nothing: the next condition, date_registration < cutoff_day, "
        "is not true for a NULL date under SQL three-valued logic, so a student with no registration date is excluded either way.",
}


def run(tests: list[str]) -> bool:
    """True if the tests pass (the mutant survived them)."""
    p = subprocess.run(PYTEST + tests, cwd=ROOT, capture_output=True, text=True)
    return p.returncode == 0


def main() -> None:
    key = sys.argv[sys.argv.index("--record") + 1] if "--record" in sys.argv else "ci"
    only = {int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")} if "--only" in sys.argv else None
    if not run([t for t in ALL if Path(ROOT / t).exists()]):
        sys.exit("the unmutated suite fails; fix that first")
    killed, survived, t0 = [], [], time.time()
    for i, (rel, old, new, targets) in enumerate(MUTANTS):
        if only is not None and i not in only:
            continue
        path = ROOT / rel
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            sys.exit(f"mutant {i}: pattern occurs {original.count(old)} times in {rel}: {old[:70]!r}")
        try:
            path.write_text(original.replace(old, new), encoding="utf-8", newline="\n")
            alive = run(targets) and run([t for t in ALL if t not in targets and Path(ROOT / t).exists()])
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")
        (survived if alive else killed).append(i)
        print(f"{i:>3} {'SURVIVED' if alive else 'killed  '} {rel}: {old[:60]!r}", flush=True)
    real_survivors = [i for i in survived if i not in EQUIVALENT]
    record = {"total": len(killed) + len(survived), "killed": len(killed), "survived": len(survived),
              "equivalent": len([i for i in survived if i in EQUIVALENT]),
              "survivors": [{"id": i, "file": MUTANTS[i][0], "original": MUTANTS[i][1][:120],
                             "equivalent_because": EQUIVALENT.get(i)} for i in survived],
              "seconds": round(time.time() - t0)}
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if key != "ci" and only is None:
        data[key] = record
        data["mutants_defined"] = len(MUTANTS)
        OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(json.dumps({k: record[k] for k in ("total", "killed", "survived", "equivalent")}))
    if "--require-all" in sys.argv and real_survivors:
        sys.exit(f"surviving mutants: {real_survivors}")


if __name__ == "__main__":
    main()
