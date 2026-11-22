"""A small lakehouse on local disk: Delta Lake tables (written with delta-rs) queried through DuckDB.

Layout: <root>/<layer>/<table> for layers bronze, silver, gold and ops. `Lake.connect()` returns a DuckDB
connection in which every Delta table is visible as <layer>.<table>, optionally pinned to earlier table
versions (Delta time travel), so that transformations stay plain SQL.
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

from .common import DEFAULT_SALT, PSEUDONYM_SALT_ENV

LAYERS = ("bronze", "silver", "gold", "ops")


class Lake:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[str, DeltaTable] = {}   # this process is the only writer

    def path(self, name: str) -> Path:
        layer, table = name.split(".")
        return self.root / layer / table

    def exists(self, name: str) -> bool:
        return (self.path(name) / "_delta_log").is_dir()

    def tables(self) -> list[str]:
        out = []
        for layer in LAYERS:
            d = self.root / layer
            if d.is_dir():
                out += [f"{layer}.{p.name}" for p in sorted(d.iterdir()) if (p / "_delta_log").is_dir()]
        return out

    def table(self, name: str, version: int | None = None) -> DeltaTable:
        if version is not None:
            return DeltaTable(str(self.path(name)), version=version)
        if name not in self._cache:
            self._cache[name] = DeltaTable(str(self.path(name)))
        return self._cache[name]

    def forget(self, name: str | None = None) -> None:
        if name is None:
            self._cache.clear()
        else:
            self._cache.pop(name, None)

    def compact(self, name: str) -> None:
        """Merge small files (the append-only operational tables gain one file per run)."""
        self.table(name).optimize.compact()
        self.forget(name)

    def version(self, name: str) -> int:
        return self.table(name).version()

    def versions(self) -> dict[str, int]:
        return {t: self.version(t) for t in self.tables()}

    def write(self, name: str, data, partition_by: list[str] | None = None, predicate: str | None = None,
              mode: str = "overwrite") -> None:
        """Overwrite a table, or only the rows matching `predicate` (a partition filter)."""
        if hasattr(data, "read_all"):
            data = data.read_all()
        if not isinstance(data, pa.Table):
            data = pa.Table.from_pandas(data, preserve_index=False)
        self.path(name).parent.mkdir(parents=True, exist_ok=True)
        kwargs = {}
        if predicate and self.exists(name):
            kwargs["predicate"] = predicate
        elif mode == "overwrite":
            kwargs["schema_mode"] = "overwrite"
        write_deltalake(str(self.path(name)), data, mode=mode, partition_by=partition_by, **kwargs)
        self.forget(name)
        table = self.table(name)
        if table.version() and table.version() % 10 == 0:
            table.create_checkpoint()   # keeps opening a table cheap as its log grows

    def connect(self, versions: dict[str, int] | None = None) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect()
        con.execute("set threads = 4")
        for layer in LAYERS:
            con.execute(f"create schema if not exists {layer}")
        con.execute("create schema if not exists run")
        self.refresh(con, versions=versions)
        con.execute(f"set variable pseudonym_salt = '{os.environ.get(PSEUDONYM_SALT_ENV, DEFAULT_SALT)}'")
        return con

    def refresh(self, con, names: list[str] | None = None, versions: dict[str, int] | None = None) -> None:
        """(Re)bind Delta tables as views; call after a write so later SQL sees the new version."""
        for name in names or self.tables():
            if not self.exists(name):
                continue
            version = (versions or {}).get(name)
            ds = self.table(name, version).to_pyarrow_dataset()
            alias = "_ds_" + name.replace(".", "__")
            con.register(alias, ds)
            con.execute(f"create or replace view {name} as select * from {alias}")


class MemLake:
    """The same interface over plain DuckDB tables in one connection: no files, no versions.

    Used where only the SQL logic matters (unit tests, the leakage detector), so the transformations run
    unchanged against a handful of hand-written rows.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection | None = None):
        self.con = con or duckdb.connect()
        for layer in LAYERS + ("run", "stage"):
            self.con.execute(f"create schema if not exists {layer}")
        self.con.execute(f"set variable pseudonym_salt = '{os.environ.get(PSEUDONYM_SALT_ENV, DEFAULT_SALT)}'")

    def tables(self) -> list[str]:
        rows = self.con.execute("select table_schema, table_name from information_schema.tables "
                                "where table_type = 'BASE TABLE' and table_schema in ('bronze', 'silver', 'gold', 'ops') "
                                "order by 1, 2").fetchall()
        return [f"{s}.{t}" for s, t in rows]

    def exists(self, name: str) -> bool:
        return name in self.tables()

    def write(self, name: str, data, partition_by: list[str] | None = None, predicate: str | None = None,
              mode: str = "overwrite") -> None:
        if hasattr(data, "read_all"):
            data = data.read_all()
        self.con.register("_incoming", data)
        if self.exists(name) and (predicate or mode == "append"):
            if predicate:
                self.con.execute(f"delete from {name} where {predicate}")
            self.con.execute(f"insert into {name} select * from _incoming")
        else:
            self.con.execute(f"create or replace table {name} as select * from _incoming")
        self.con.unregister("_incoming")

    def refresh(self, con=None, names=None, versions=None) -> None:
        return None

    def connect(self, versions=None) -> duckdb.DuckDBPyConnection:
        return self.con
