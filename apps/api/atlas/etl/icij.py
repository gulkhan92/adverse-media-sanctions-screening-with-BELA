"""ICIJ Offshore Leaks loader into the ``icij`` schema.

Licence: database ODbL, contents CC BY-SA. Keep these tables separate from the
knowledge base, do not redistribute, and show the ICIJ disclaimer wherever shown.
"""

from __future__ import annotations

import csv
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from atlas.db import connect, copy_rows
from atlas.names import normalize_name

NODE_FILES = {
    "entity": "nodes-entities.csv",
    "officer": "nodes-officers.csv",
    "intermediary": "nodes-intermediaries.csv",
    "address": "nodes-addresses.csv",
    "other": "nodes-others.csv",
}
NODE_COLUMNS = [
    "node_id",
    "node_type",
    "name",
    "name_norm",
    "countries",
    "country_codes",
    "jurisdiction",
    "company_type",
    "status",
    "source_id",
    "incorporation_date",
    "note",
]
REL_COLUMNS = [
    "src_id",
    "dst_id",
    "rel_type",
    "link",
    "status",
    "start_date",
    "end_date",
    "source_id",
]


def _none(value: str | None) -> str | None:
    return value.strip() or None if value is not None else None


def node_rows(directory: Path, seen: set[int]) -> Iterator[tuple[Any, ...]]:
    csv.field_size_limit(sys.maxsize)
    for node_type, filename in NODE_FILES.items():
        kind = "person" if node_type == "officer" else "organization"
        with (directory / filename).open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                node_id = int(row["node_id"])
                if node_id in seen:
                    continue
                seen.add(node_id)
                name = _none(row.get("name"))
                norm = (
                    normalize_name(name, kind).stripped if name and node_type != "address" else None
                )
                yield (
                    node_id,
                    node_type,
                    name,
                    norm,
                    _none(row.get("countries")),
                    _none(row.get("country_codes")),
                    _none(row.get("jurisdiction")),
                    _none(row.get("company_type")),
                    _none(row.get("status")),
                    _none(row.get("sourceID")),
                    _none(row.get("incorporation_date")),
                    _none(row.get("note")),
                )


def relationship_rows(directory: Path, valid: set[int]) -> Iterator[tuple[Any, ...]]:
    with (directory / "relationships.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            src, dst = int(row["node_id_start"]), int(row["node_id_end"])
            if src in valid and dst in valid:
                yield (
                    src,
                    dst,
                    _none(row.get("rel_type")),
                    _none(row.get("link")),
                    _none(row.get("status")),
                    _none(row.get("start_date")),
                    _none(row.get("end_date")),
                    _none(row.get("sourceID")),
                )


def load(directory: Path) -> dict[str, int]:
    seen: set[int] = set()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE icij.relationships, icij.nodes RESTART IDENTITY")
        nodes = copy_rows(conn, "icij.nodes", NODE_COLUMNS, node_rows(directory, seen))
        rels = copy_rows(
            conn, "icij.relationships", REL_COLUMNS, relationship_rows(directory, seen)
        )
        conn.commit()
    return {"nodes": nodes, "relationships": rels}


if __name__ == "__main__":
    from atlas.config import RAW_DIR

    print(load(RAW_DIR / "leaks" / "icij"))
