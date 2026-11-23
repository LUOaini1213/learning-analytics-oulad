# From stakeholder questions to data, measures and visuals

Personal portfolio project; not affiliated with or endorsed by ITE or the Open University.

**Everything about the stakeholders on this page is an ASSUMPTION.** Nobody was interviewed. The three personas are
hypothetical, written to show how a question is turned into a data requirement, a measure and a visual, and to give
the three report pages a reason to differ. In a real engagement this page would be the output of interviews and
would be signed off before any dashboard was built.

## Personas (ASSUMPTION)

| Persona | Role | What they decide | How often | What they must not see |
|---|---|---|---|---|
| P1 Lecturer | Teaches one or two modules | Which students to contact this week | Weekly, in term | Other lecturers' students; protected attributes |
| P2 Programme or college manager | Oversees several modules | Where to put support effort; which modules to review | Each term, and at checkpoints | Any individual student |
| P3 Data engineer | Runs the pipeline | Whether this week's load can be published; what to fix | Every run | n/a (sees everything) |

## P1 Lecturer

| # | Question (ASSUMPTION) | Data requirement | Gold tables | Measure | Visual (page "My module this week") |
|---|---|---|---|---|---|
| L1 | Who in my class has gone quiet? | Clicks per student per week, as of now; registration status as of now | `fact_student_week`, `dim_student_presentation` | Inactive Students, Days Since Last Active | Card; student table sorted by risk |
| L2 | Who has missed an assessment? | Due dates; submissions dated before today; banked results | `dim_assessment`, `fact_assessment` | Students Missing an Assessment | Card; column in student table |
| L3 | If I can only contact some students, who first? | A ranking learned from earlier presentations, using only what is known today | `features_early_warning`, model scores | Flagged Students, risk score | Student table with flag and reasons |
| L4 | Why is this student flagged? | Per-student contributions from a transparent model | model scores | Reason 1 to 3 | Columns in student table |
| L5 | Is the class as a whole engaging? | Active students over enrolled students, by week | `fact_student_week` aggregated | Active Share | Line chart by week |
| L6 | What are students using? | Clicks by type of activity | `fact_engagement_weekly`, `dim_activity_type` | Activity Share | Bar chart |

Rules that follow from the questions: only data dated before the cut-off may be used (L1 to L4); a student who has
already left is not listed; the flag is limited to a share of the class the lecturer can realistically contact
(20% assumed).

## P2 Programme or college manager

| # | Question (ASSUMPTION) | Data requirement | Source | Measure | Visual (page "Programme overview") |
|---|---|---|---|---|---|
| M1 | How many students do we lose, and when? | Registrations and unregistration dates | `dim_student_presentation` | Withdrawal Rate, Still Registered Share | Cards; curve by week and module |
| M2 | Which modules need a closer look? | Final results by module-presentation | `dim_student_presentation` | At-risk Rate, Pass Rate | Bar chart by module-presentation |
| M3 | Do outcomes differ between groups of learners? | Results by one attribute at a time, never crossed | `dim_student_presentation` | Group At-risk Rate | Table by attribute and group |
| M4 | Can I trust that no individual can be identified? | Counts below 5 withheld; no way to recover them from totals | access views | Cells Withheld | Card; blank cells |

Rules: aggregates only; any count below 5 is withheld together with any rate that would reveal it; attributes are
shown one at a time, because crossing them produces small cells.

## P3 Data engineer

| # | Question | Data requirement | Source | Measure | Visual (page "Pipeline health") |
|---|---|---|---|---|---|
| E1 | Did every weekly load run, and how long did it take? | One row per run with status and duration | `ops.run_log` | Runs, Runs Stopped, Run Seconds | Cards; run table |
| E2 | Did the volume look normal? | Rows loaded per run | `ops.run_log`, batch ledger | Rows Loaded | Column chart by week |
| E3 | Which checks failed? | One row per check per run | `ops.dq_results` | Checks Run, Checks Failed, Check Pass Rate | Bar chart by kind; table of open warnings |
| E4 | Are rejected rows piling up? | Quarantine size after each run | `silver.quarantine`, `ops.run_log` | Peak Quarantine Rows | Card; column in run table |

## Requirements that are not met

- No requirement was validated with a user, so priorities are guesses.
- The lecturer page is fixed to four cut-off days (14, 28, 56, 84). A real weekly view would refresh every week.
- There is no feedback loop: nothing records whether a contacted student was helped. Without that, the flag cannot
  be evaluated on what matters.
- Accessibility of the report (colour contrast, keyboard order, screen-reader text) was not assessed, because the
  report was not rendered.
