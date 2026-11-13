"""Load the Wikidata subset (parquet from data/scripts) into entities and aliases."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from atlas.db import connect, copy_rows
from atlas.names import normalize_name, script_of
from atlas.quality import ISO_ALPHA2

SOURCE = "wikidata"
COUNTRY_PROPS = ("P27", "P17", "P495")
REL_PROPS = {"P749": "parent_organization", "P127": "owned_by"}
_TIME = re.compile(r"^[+-](\d{4})-(\d{2})-(\d{2})")


def birth_date(value: str) -> str | None:
    match = _TIME.match(value)
    if not match:
        return None
    year, month, day = match.groups()
    if month == "00":
        return year
    return f"{year}-{month}-{day}" if day != "00" else f"{year}-{month}"


def load(directory: Path) -> dict[str, int]:
    ents = pq.read_table(directory / "entities.parquet").to_pylist()
    aliases = pq.read_table(directory / "aliases.parquet").to_pylist()
    claims = pq.read_table(directory / "claims.parquet").to_pylist()
    iso = json.loads((directory / "countries.json").read_text())

    by_qid: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for c in claims:
        by_qid[c["qid"]][c["property"]].append(c["value"])
    alias_by_qid: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for a in aliases:
        alias_by_qid[a["qid"]].append((a["lang"], a["alias"]))

    entity_rows: list[tuple[Any, ...]] = []
    seen_lei: set[str] = set()
    names: dict[str, dict[str, str]] = {}
    for e in ents:
        labels = json.loads(e["labels"])
        descs = json.loads(e["descriptions"])
        name = labels.get("en") or next(iter(labels.values()))
        n = normalize_name(name, e["kind"])
        if not n.full:
            continue
        props = by_qid[e["qid"]]
        countries = sorted(
            {
                iso[v]
                for p in COUNTRY_PROPS
                for v in props.get(p, [])
                if v in iso and iso[v] in ISO_ALPHA2  # drops historic codes such as DD
            }
        )
        lei = next((v for v in props.get("P1278", []) if v not in seen_lei), None)
        if lei:
            seen_lei.add(lei)
        dates = [d for d in (birth_date(v) for v in props.get("P569", [])) if d]
        description = descs.get("en") or next(iter(descs.values()), None)
        entity_rows.append(
            (
                e["qid"],
                lei,
                e["kind"],
                name,
                n.stripped,
                n.key,
                description,
                countries,
                dates[0] if dates else None,
                e["sitelinks"],
                SOURCE,
            )
        )
        names[e["qid"]] = labels

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM entities WHERE source = %s", (SOURCE,))
            cur.execute(
                "DELETE FROM entity_embeddings WHERE ref_type IN ('entity', 'entity_alias')"
            )
        copy_rows(
            conn,
            "entities",
            [
                "qid",
                "lei",
                "kind",
                "name",
                "name_norm",
                "name_key",
                "description",
                "countries",
                "birth_date",
                "sitelinks",
                "source",
            ],
            entity_rows,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT qid, id, kind FROM entities WHERE source = %s", (SOURCE,))
            ids = {qid: (eid, kind) for qid, eid, kind in cur.fetchall()}

        alias_rows: list[tuple[Any, ...]] = []
        for qid, (eid, kind) in ids.items():
            seen: set[tuple[str, str | None]] = set()
            candidates = [(lg, label, lg == "en") for lg, label in names[qid].items()]
            candidates += [(lg, alias, False) for lg, alias in alias_by_qid.get(qid, [])]
            for lang, text, primary in candidates:
                n = normalize_name(text, kind)
                if not n.full or (n.stripped, lang) in seen:
                    continue
                seen.add((n.stripped, lang))
                alias_rows.append(
                    (eid, text, n.stripped, n.key, lang, script_of(text), SOURCE, primary)
                )
        aliases_loaded = copy_rows(
            conn,
            "entity_aliases",
            [
                "entity_id",
                "alias",
                "alias_norm",
                "alias_key",
                "lang",
                "script",
                "source",
                "is_primary",
            ],
            alias_rows,
        )

        rel_rows: set[tuple[int, int, str, str]] = set()
        for qid, props in by_qid.items():
            if qid not in ids:
                continue
            for prop, rel in REL_PROPS.items():
                for target in props.get(prop, []):
                    if target in ids:
                        rel_rows.add((ids[qid][0], ids[target][0], rel, SOURCE))
            for child in props.get("P355", []):
                if child in ids:
                    rel_rows.add((ids[child][0], ids[qid][0], "parent_organization", SOURCE))
        rels = copy_rows(
            conn,
            "entity_relationships",
            ["src_id", "dst_id", "rel_type", "source"],
            sorted(rel_rows),
        )
        conn.commit()
    return {"entities": len(entity_rows), "aliases": aliases_loaded, "relationships": rels}


if __name__ == "__main__":
    from atlas.config import PROCESSED_DIR

    print(load(PROCESSED_DIR / "wikidata"))
