"""GLEIF Golden Copy loader (Level 1 LEI records, Level 2 relationships).

Selection rules keep the entity table near 300 thousand rows. An active record is kept when
  (a) its LEI is already linked to a Wikidata item (property P1278),
  (b) its normalized legal name equals a watchlist entry or alias, or
  (c) its legal address country is not one of the large domestic registries listed in
      LARGE_REGISTRIES (offshore, high-risk, and long-tail jurisdictions are kept).
Records matching (a) are merged into the existing Wikidata entity instead of duplicated.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import Any

from atlas.db import connect, copy_rows
from atlas.names import normalize_name, script_of

SOURCE = "gleif"
LARGE_REGISTRIES = frozenset(
    "IN DE IT US NL ES FR GB SE DK FI BE NO AT AU CA PL IE EE CZ HU PT JP".split()
)
_OTHER = re.compile(r"^Entity\.(Transliterated)?OtherEntityNames?\..*Name\.(\d+)$")


def name_columns(header: list[str]) -> list[tuple[int, int | None]]:
    """Return (name column, language column) pairs for legal, other, and transliterated names."""
    index = {name: i for i, name in enumerate(header)}
    pairs: list[tuple[int, int | None]] = [
        (index["Entity.LegalName"], index.get("Entity.LegalName.xmllang"))
    ]
    for name, i in index.items():
        if _OTHER.match(name):
            pairs.append((i, index.get(f"{name}.xmllang")))
    return pairs


def load_level1(
    path: Path, wikidata_leis: set[str], watchlist_names: set[str], max_rows: int = 0
) -> dict[str, Any]:
    csv.field_size_limit(sys.maxsize)
    new_rows: list[tuple[Any, ...]] = []
    alias_by_lei: dict[str, list[tuple[str, str | None]]] = {}
    merged_country: dict[str, set[str]] = {}
    stats = {
        "scanned": 0,
        "active": 0,
        "kept_wikidata": 0,
        "kept_watchlist": 0,
        "kept_jurisdiction": 0,
    }
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        col = {name: i for i, name in enumerate(header)}
        pairs = name_columns(header)
        i_lei, i_name = col["LEI"], col["Entity.LegalName"]
        i_country, i_hq = (
            col["Entity.LegalAddress.Country"],
            col["Entity.HeadquartersAddress.Country"],
        )
        i_status, i_reg = col["Entity.EntityStatus"], col["Registration.RegistrationStatus"]
        i_cat = col["Entity.EntityCategory"]
        for row in reader:
            stats["scanned"] += 1
            if row[i_status] != "ACTIVE" or row[i_reg] != "ISSUED":
                continue
            stats["active"] += 1
            lei, name = row[i_lei], row[i_name]
            norm = normalize_name(name, "organization")
            if lei in wikidata_leis:
                reason = "kept_wikidata"
            elif norm.stripped in watchlist_names:
                reason = "kept_watchlist"
            elif row[i_country] not in LARGE_REGISTRIES:
                reason = "kept_jurisdiction"
            else:
                continue
            stats[reason] += 1
            countries = sorted({c for c in (row[i_country], row[i_hq]) if c})
            names = [
                (row[n], row[lg] or None if lg is not None else None) for n, lg in pairs if row[n]
            ]
            alias_by_lei[lei] = names
            if reason == "kept_wikidata":
                merged_country[lei] = set(countries)
            else:
                new_rows.append(
                    (
                        lei,
                        "organization",
                        name,
                        norm.stripped,
                        norm.key,
                        row[i_cat] or None,
                        countries,
                        SOURCE,
                    )
                )
            if max_rows and stats["active"] >= max_rows:
                break
    stats["new_rows"] = len(new_rows)
    return {
        "stats": stats,
        "new_rows": new_rows,
        "aliases": alias_by_lei,
        "merged_country": merged_country,
    }


def load_level2(path: Path, lei_to_id: dict[str, int]) -> list[tuple[Any, ...]]:
    csv.field_size_limit(sys.maxsize)
    rows: set[tuple[Any, ...]] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        a, b = (
            header.index("Relationship.StartNode.NodeID"),
            header.index("Relationship.EndNode.NodeID"),
        )
        t = header.index("Relationship.RelationshipType")
        s = header.index("Relationship.RelationshipStatus")
        for row in reader:
            if row[s] != "ACTIVE":
                continue
            src, dst = lei_to_id.get(row[a]), lei_to_id.get(row[b])
            if src and dst and src != dst:
                rows.add((src, dst, row[t].lower(), SOURCE))
    return sorted(rows)


def load(directory: Path, max_rows: int = 0) -> dict[str, Any]:
    level1 = next(directory.glob("*lei2-golden-copy.csv"))
    level2 = next(directory.glob("*rr-golden-copy.csv"))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT lei FROM entities WHERE lei IS NOT NULL AND source <> %s", (SOURCE,)
            )
            wikidata_leis = {r[0] for r in cur.fetchall()}
            cur.execute(
                "SELECT name_norm FROM watchlist_entries "
                "UNION SELECT alias_norm FROM watchlist_aliases"
            )
            watchlist_names = {r[0] for r in cur.fetchall()}
            cur.execute("DELETE FROM entities WHERE source = %s", (SOURCE,))
            cur.execute(
                "DELETE FROM entity_embeddings WHERE ref_type IN ('entity', 'entity_alias')"
            )
        result = load_level1(level1, wikidata_leis, watchlist_names, max_rows)
        copy_rows(
            conn,
            "entities",
            ["lei", "kind", "name", "name_norm", "name_key", "description", "countries", "source"],
            result["new_rows"],
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT lei, id FROM entities WHERE lei = ANY(%s)", (list(result["aliases"]),)
            )
            lei_to_id = {lei: eid for lei, eid in cur.fetchall()}
            cur.executemany(
                "UPDATE entities SET countries = (SELECT array_agg(DISTINCT c) FROM "
                "unnest(countries || %s::text[]) c) WHERE lei = %s",
                [(sorted(v), k) for k, v in result["merged_country"].items()],
            )
        alias_rows: list[tuple[Any, ...]] = []
        for lei, names in result["aliases"].items():
            eid = lei_to_id.get(lei)
            if eid is None:
                continue
            seen: set[tuple[str, str | None]] = set()
            for position, (text, lang) in enumerate(names):
                n = normalize_name(text, "organization")
                if not n.full or (n.stripped, lang) in seen:
                    continue
                seen.add((n.stripped, lang))
                alias_rows.append(
                    (eid, text, n.stripped, n.key, lang, script_of(text), SOURCE, position == 0)
                )
        # Stage then insert so aliases already present on merged Wikidata entities are skipped.
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE stage_aliases (LIKE entity_aliases INCLUDING DEFAULTS) "
                "ON COMMIT DROP"
            )
        copy_rows(
            conn,
            "stage_aliases",
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
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO entity_aliases (entity_id, alias, alias_norm, alias_key, lang,"
                " script, source, is_primary) SELECT DISTINCT ON (entity_id, alias_norm, lang)"
                " entity_id, alias, alias_norm, alias_key, lang, script, source, is_primary"
                " FROM stage_aliases ON CONFLICT DO NOTHING"
            )
            aliases = cur.rowcount
        # Level 2 needs ids for every kept LEI, including those merged into Wikidata entities.
        rels = copy_rows(
            conn,
            "entity_relationships",
            ["src_id", "dst_id", "rel_type", "source"],
            load_level2(level2, lei_to_id),
        )
        conn.commit()
    return {**result["stats"], "aliases": aliases, "relationships": rels}


if __name__ == "__main__":
    from atlas.config import RAW_DIR

    print(load(RAW_DIR / "registry" / "gleif"))
