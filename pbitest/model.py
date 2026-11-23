"""Load the CSV extracts into the tables the semantic model declares, and apply a security role.

Loading mirrors the Power Query partitions (Csv.Document, PromoteHeaders, TransformColumnTypes with the
en-US culture): text stays text and an empty field in a number column becomes null.

`with_role` applies a role's row filters the way row-level security does: the filtered rows of a table on
the one side of a relationship limit the tables on the many side. Only the three filter patterns used in
this model are understood (see ROLE_PATTERNS); anything else raises an error instead of guessing.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pandas as pd

from .tmdl import SemanticModel, _read_expression, parse_folder

ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / "powerbi" / "LearningAnalytics.SemanticModel" / "definition"
DATA = ROOT / "dashboard" / "data"

ROLE_PATTERNS = {
    "none": re.compile(r"^FALSE\(\)$"),
    "own": re.compile(r"^(\w+)\[(\w+)\] = USERNAME\(\)$"),
    "lookup": re.compile(r"^CONTAINS\((\w+), (\w+)\[(\w+)\], USERNAME\(\), (\w+)\[(\w+)\], (\w+)\[(\w+)\]\)$"),
}


def _cast(series, data_type):
    if data_type == "string":
        return series.astype(object)
    if data_type == "int64":
        return pd.to_numeric(series.mask(series == ""), errors="raise").astype("Int64")
    if data_type in ("double", "decimal"):
        return pd.to_numeric(series.mask(series == ""), errors="raise").astype(float)
    raise ValueError(f"unsupported data type {data_type}")


def parse_roles(definition: Path) -> dict[str, dict[str, str]]:
    """{role: {table: DAX row filter}} from definition/roles/*.tmdl."""
    roles = {}
    for path in sorted((Path(definition) / "roles").glob("*.tmdl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        name, perms, i = None, {}, 0
        while i < len(lines):
            s = lines[i].strip()
            if lines[i].startswith("role "):
                name = s.split(None, 1)[1]
            elif s.startswith("tablePermission "):
                table, rest = s[len("tablePermission "):].split("=", 1)
                expr, i = _read_expression(lines, i, rest, 1)
                perms[table.strip()] = expr
                continue
            i += 1
        roles[name] = perms
    return roles


class LoadedModel:
    def __init__(self, definition=DEFINITION, data=DATA):
        self.meta: SemanticModel = parse_folder(definition)
        self.roles = parse_roles(definition)
        self.frames = {}
        for name, t in self.meta.tables.items():
            raw = pd.read_csv(Path(data) / t.csv_file, dtype=str, keep_default_na=False)
            missing = [c.source_column for c in t.columns.values() if c.source_column not in raw.columns]
            if missing:
                raise ValueError(f"{t.csv_file} lacks columns {missing} declared in table {name}")
            self.frames[name] = pd.DataFrame({c.name: _cast(raw[c.source_column], c.data_type) for c in t.columns.values()})
        self.relationships = self.meta.relationships
        self.measures = self.meta.measures
        self.date_tables = {}

    def role_rows(self, table: str, expression: str, username: str) -> pd.Series:
        """Boolean mask of the rows of `table` a role filter lets through."""
        df = self.frames[table]
        e = " ".join(expression.split())
        if ROLE_PATTERNS["none"].match(e):
            return pd.Series(False, index=df.index)
        m = ROLE_PATTERNS["own"].match(e)
        if m and m.group(1) == table:
            return df[m.group(2)] == username
        m = ROLE_PATTERNS["lookup"].match(e)
        if m and m.group(1) == m.group(2) == m.group(4) and m.group(6) == table:
            sec = self.frames[m.group(1)]
            allowed = set(sec.loc[sec[m.group(3)] == username, m.group(5)])
            return df[m.group(7)].isin(allowed)
        raise ValueError(f"role filter outside the supported patterns: {expression}")

    def with_role(self, role: str, username: str) -> "LoadedModel":
        """A copy of the model as a member of `role` signed in as `username` would see it."""
        if role not in self.roles:
            raise KeyError(role)
        out = copy.copy(self)
        out.frames = dict(self.frames)
        limited = set()
        for table, expr in self.roles[role].items():
            out.frames[table] = self.frames[table][self.role_rows(table, expr, username)]
            limited.add(table)
        changed = True
        while changed:      # security filters travel from the one side to the many side
            changed = False
            for r in self.relationships:
                if r.to_table in limited:
                    keep = out.frames[r.from_table][r.from_column].isin(set(out.frames[r.to_table][r.to_column]))
                    if not keep.all():
                        out.frames[r.from_table] = out.frames[r.from_table][keep]
                        changed = True
                    if r.from_table not in limited:
                        limited.add(r.from_table)
                        changed = True
        return out
