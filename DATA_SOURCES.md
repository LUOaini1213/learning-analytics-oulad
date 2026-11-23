# Data sources

Personal portfolio project; not affiliated with or endorsed by ITE, the Open University or Microsoft.
Every external source used in this repository is listed here with where it was read, when, and under what licence.

## Data

| Source | Publisher | URL | Accessed | Licence | Used for |
|---|---|---|---|---|---|
| Open University Learning Analytics Dataset (OULAD), archive `open+university+learning+analytics+dataset.zip` | The Open University (Knowledge Media Institute), distributed by the UCI Machine Learning Repository | https://archive.ics.uci.edu/dataset/349/open+university+learning+analytics+dataset | 2026-11-23 | CC BY 4.0 | Everything. Downloaded by `scripts/fetch_data.py`; hashes in `data/source_manifest.json` |
| OULAD description page (table and column definitions) | The Open University | https://research.stem.open.ac.uk/ouanalyse/open-dataset-more/ | 2026-11-23 | page content; dataset "released under CC-BY 4.0 license" | Column meanings, the exam-date rule, the banked-assessment flag |
| OULAD download page | The Open University | https://research.stem.open.ac.uk/ouanalyse/dataset/ | 2026-11-23 | n/a | Confirms the publisher's own download link. The address given in the project brief, `analyse.kmi.open.ac.uk/open_dataset`, now redirects to `research.stem.open.ac.uk/ouanalyse/` |

Licence statements as read:

- UCI page: "This dataset is licensed under a Creative Commons Attribution 4.0 International (CC BY 4.0) license."
- Open University description page: "This dataset is released under CC-BY 4.0 license."

Citation requested by the publisher: Kuzilek J., Hlosta M., Zdrahal Z. Open University Learning Analytics dataset.
Sci. Data 4:170171 doi: 10.1038/sdata.2017.171 (2017).

The journal article itself could not be opened from the build machine (the publisher's site redirected to a sign-in
page), so nothing in this repository is quoted from it; statements about the data come from the two pages above
and from the data. **UNVERIFIED:** the article's account of how student numbers were anonymised.

Definitions relied on, as read on the Open University description page on 2026-11-23:

- `date_unregistration`: "the student's unregistered date from the module presentation, this is the number of days
  measured relative to the start of the module-presentation."
- `is_banked`: "a status flag indicating that the assessment result has been transferred from a previous presentation."
- exam dates: "If the information about the final exam date is missing, it is at the end of the last presentation week."
- `sum_click`: "the number of times a student interacts with the material in that day."

### What is redistributed here

CC BY 4.0 allows copying and adaptation with attribution. This repository contains:

- `data/fixture/*.csv`: every row belonging to 160 student registrations in four module-presentations, unchanged
  (a sample for offline tests);
- `dashboard/data/*.csv`: derived tables. `student_snapshot.csv` is student-level with salted-hash keys for
  presentations starting in October 2014; the other tables are aggregates with counts below 5 withheld.

The full raw files are not committed. The documentation file shipped inside the archive (`OULAD.names`) is not
committed either, because it contains the contact addresses of the dataset's authors.

## Specifications and schemas

| Source | Publisher | URL | Accessed | Licence | Used for |
|---|---|---|---|---|---|
| OpenLineage specification 2-0-2 (`OpenLineage.json`) | OpenLineage project (LF AI & Data) | https://openlineage.io/spec/2-0-2/OpenLineage.json | 2026-11-23 | Apache-2.0 | `tests/schemas/OpenLineage-2-0-2.json`; the lineage events are validated against it |
| Power BI base theme `CY24SU10.json` | Microsoft | shipped with Power BI Desktop; copied from the author's earlier project sg-transport-powerbi | 2026-11-23 | part of the PBIP file format | `powerbi/LearningAnalytics.Report/StaticResources` |
| Microsoft JSON schemas for PBIR, commit 49f4a76 | Microsoft | https://github.com/microsoft/json-schemas | 2026-11-23 (local copy dated 2026-10-12) | MIT | One-off validation of the PBIR files; not committed |
| Microsoft.AnalysisServices (Tabular Object Model) 19.117.0 | Microsoft | https://www.nuget.org/packages/Microsoft.AnalysisServices | 2026-11-23 | Microsoft library licence | One-off read and validation of the TMDL folder; not committed |

## Microsoft Learn pages (for docs/fabric_mapping.md)

| Page | URL | Accessed |
|---|---|---|
| Implement medallion lakehouse architecture in Fabric | https://learn.microsoft.com/en-us/fabric/onelake/onelake-medallion-lakehouse-architecture | 2026-11-23 |
| What is a lakehouse? | https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-overview | 2026-11-23 |
| Row-level security (RLS) with Power BI | https://learn.microsoft.com/en-us/fabric/security/service-admin-row-level-security | 2026-11-23 |
| Lineage in Fabric | https://learn.microsoft.com/en-us/fabric/governance/lineage | 2026-11-23 |

## Code reused

`pbitest/dax.py` and `pbitest/tmdl.py` and the report-building helpers in `scripts/build_powerbi.py` are adapted
from the author's own earlier repository sg-transport-powerbi (MIT).
