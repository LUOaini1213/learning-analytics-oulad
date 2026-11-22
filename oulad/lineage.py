"""Table-level lineage in the OpenLineage run-event shape, derived from the SQL each step runs.

Inputs are found by scanning the SQL for layer-qualified table names. This is table-level only: there is no
column-level lineage, and no OpenLineage backend (such as Marquez) was run; the events are JSON files.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid

PRODUCER = "https://github.com/LUOaini1213/learning-analytics-oulad"
SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
NAMESPACE = "oulad-lake"
REF = re.compile(r"\b(bronze|silver|gold|ops|stage)\.([a-z_][a-z0-9_]*)\b")


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def referenced_tables(sql: str, stage_sql: dict[str, str] | None = None) -> list[str]:
    """Layer-qualified tables a statement reads. A reference to a stage view is replaced by what it reads."""
    out: list[str] = []
    for layer, table in REF.findall(strip_comments(sql)):
        name = f"{layer}.{table}"
        if layer == "stage":
            for inner in referenced_tables((stage_sql or {}).get(table, ""), stage_sql):
                if inner not in out:
                    out.append(inner)
        elif name not in out:
            out.append(name)
    return out


def run_event(run_id: str, job: str, inputs: list[str], output: str, fields: list[tuple[str, str]],
              row_count: int, event_time: str | None = None) -> dict:
    facet = {"_producer": PRODUCER}
    return {
        "eventType": "COMPLETE",
        "eventTime": event_time or dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
        "producer": PRODUCER,
        "schemaURL": SCHEMA_URL,
        "run": {"runId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{PRODUCER}/{run_id}/{job}")),
                "facets": {"parent": {**facet,
                                      "_schemaURL": "https://openlineage.io/spec/facets/1-0-1/ParentRunFacet.json#/$defs/ParentRunFacet",
                                      "run": {"runId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{PRODUCER}/{run_id}"))},
                                      "job": {"namespace": NAMESPACE, "name": "weekly_load"}}}},
        "job": {"namespace": NAMESPACE, "name": job},
        "inputs": [{"namespace": NAMESPACE, "name": n} for n in inputs if n != output],
        "outputs": [{
            "namespace": NAMESPACE, "name": output,
            "facets": {"schema": {**facet,
                                  "_schemaURL": "https://openlineage.io/spec/facets/1-1-1/SchemaDatasetFacet.json#/$defs/SchemaDatasetFacet",
                                  "fields": [{"name": n, "type": t} for n, t in fields]}},
            "outputFacets": {"outputStatistics": {**facet,
                                                  "_schemaURL": "https://openlineage.io/spec/facets/1-0-2/OutputStatisticsOutputDatasetFacet.json#/$defs/OutputStatisticsOutputDatasetFacet",
                                                  "rowCount": int(row_count)}},
        }],
    }


def edges(events: list[dict]) -> list[tuple[str, str]]:
    out = []
    for e in events:
        for o in e["outputs"]:
            for i in e["inputs"]:
                if (i["name"], o["name"]) not in out:
                    out.append((i["name"], o["name"]))
    return out


def mermaid(events: list[dict]) -> str:
    """A left-to-right flowchart, one subgraph per layer."""
    es = edges(events)
    nodes = sorted({n for e in es for n in e})
    ident = lambda n: n.replace(".", "_")
    lines = ["flowchart LR"]
    for layer in ("landing", "bronze", "silver", "gold", "ops"):
        members = [n for n in nodes if n.split(".")[0] == layer]
        if members:
            lines.append(f"  subgraph {layer}")
            lines += [f"    {ident(n)}[\"{n.split('.', 1)[1]}\"]" for n in members]
            lines.append("  end")
    lines += [f"  {ident(a)} --> {ident(b)}" for a, b in sorted(es)]
    return "\n".join(lines)
