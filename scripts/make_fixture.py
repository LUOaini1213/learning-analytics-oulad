"""Cut a small sample of the real extract for offline tests (data/fixture, redistributed under CC BY 4.0).

Usage: python scripts/make_fixture.py [raw_dir]
Takes every row belonging to a deterministic sample of students from four module-presentations (two from 2013,
two from 2014). Students who appear in more than one of the four are taken first, so the fixture exercises
the grouped train/test split.
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
PRESENTATIONS = [("AAA", "2013J", 45), ("AAA", "2014J", 45), ("GGG", "2013J", 35), ("GGG", "2014J", 35)]


def main(raw: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    for t in ("courses", "assessments", "vle", "studentInfo", "studentRegistration", "studentAssessment",
              "studentVle"):
        con.execute(f"create view {t} as select *, row_number() over () as _rn from "
                    f"read_csv('{(raw / (t + '.csv')).as_posix()}', all_varchar = true, header = true)")
    con.execute("create table mp (code_module varchar, code_presentation varchar, n integer)")
    con.executemany("insert into mp values (?, ?, ?)", PRESENTATIONS)
    con.execute("""
        create table cand as
        select i.code_module, i.code_presentation, i.id_student, mp.n,
               count(*) over (partition by i.id_student) as appearances
        from studentInfo i join mp using (code_module, code_presentation)""")
    con.execute("""
        create table pick as
        select code_module, code_presentation, id_student from (
            select *, row_number() over (partition by code_module, code_presentation
                                         order by appearances desc, hash(id_student)) as k from cand)
        where k <= n""")

    def dump(name: str, select: str) -> None:
        target = (out / f"{name}.csv").as_posix()
        con.execute(f"copy ({select}) to '{target}' (format csv, header true, force_quote *)")

    for t in ("courses", "assessments", "vle"):
        dump(t, f"select s.* exclude (_rn) from {t} s semi join mp using (code_module, code_presentation) "
                "order by _rn")
    for t in ("studentInfo", "studentRegistration", "studentVle"):
        dump(t, f"select s.* exclude (_rn) from {t} s semi join pick using (code_module, code_presentation, "
                "id_student) order by _rn")
    dump("studentAssessment", """
        select s.* exclude (_rn) from studentAssessment s join assessments a using (id_assessment)
        semi join pick p on p.code_module = a.code_module and p.code_presentation = a.code_presentation
                         and p.id_student = s.id_student
        order by s._rn""")
    for p in sorted(out.glob("*.csv")):
        with open(p, "rb") as f:
            print(p.name, sum(1 for _ in f) - 1, p.stat().st_size)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "raw", ROOT / "data" / "fixture")
