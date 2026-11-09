"""Build a Wikidata subset of people and organizations as parquet files.

Streams the official JSON dump (https://dumps.wikimedia.org/wikidatawiki/entities/
latest-all.json.gz) or a local copy given with --input. Keeps humans (Q5) and
organizations (Q43229 and subclasses) whose sitelink count meets --min-sitelinks,
plus every QID listed in --include-qids (for example those matched to watchlists).

Outputs under data/processed/wikidata/:
  entities.parquet     qid, kind, sitelinks, labels, descriptions (JSON by language)
  aliases.parquet      qid, lang, alias
  claims.parquet       qid, property, value (item ids and time/string values)

Verify the class and property identifiers on wikidata.org before relying on them.
Licence: CC0.
"""

from __future__ import annotations

import argparse
import bz2
import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from common import PROCESSED_DIR, get_logger, record_file

SOURCE_KEY = "wikidata"
DUMP_URL = "https://dumps.wikimedia.org/wikidatawiki/entities/latest-all.json.gz"
LANGS = ("en", "es", "fr", "de", "ar", "ru", "tr")
HUMAN = "Q5"
ORG_ROOTS = {"Q43229", "Q4830453", "Q783794", "Q22687", "Q7278", "Q327333"}
PROPERTIES = ("P31", "P279", "P27", "P106", "P39", "P569", "P1278", "P127", "P749", "P355")


def open_dump(path: Path) -> Iterator[dict[str, Any]]:
    """Yield entities from a Wikidata JSON dump (one entity per line inside a JSON array)."""
    opener = bz2.open if path.suffix == ".bz2" else gzip.open
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            line = line.strip().rstrip(",")
            if line in ("[", "]", ""):
                continue
            yield json.loads(line)


def claim_values(entity: dict[str, Any], prop: str) -> list[str]:
    values: list[str] = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        value = snak["datavalue"]["value"]
        if isinstance(value, dict):
            value = value.get("id") or value.get("time") or value.get("text")
        if value is not None:
            values.append(str(value))
    return values


def subclass_closure(roots: set[str], parents: dict[str, list[str]]) -> set[str]:
    """Return roots plus every class that reaches a root through P279 edges."""
    children: dict[str, list[str]] = {}
    for child, ps in parents.items():
        for parent in ps:
            children.setdefault(parent, []).append(child)
    seen = set(roots)
    stack = list(roots)
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def classify(p31: list[str], org_classes: set[str]) -> str | None:
    if HUMAN in p31:
        return "person"
    if any(cls in org_classes for cls in p31):
        return "organization"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="local dump (.json.gz or .json.bz2)")
    parser.add_argument("--min-sitelinks", type=int, default=20)
    parser.add_argument("--include-qids", type=Path, help="text file, one QID per line")
    parser.add_argument("--limit", type=int, default=0, help="stop after N entities (testing)")
    args = parser.parse_args()
    log = get_logger(SOURCE_KEY)
    if not args.input:
        log.error(f"no --input given; download {DUMP_URL} first (about 140 GB compressed)")
        raise SystemExit(2)
    forced = set(args.include_qids.read_text().split()) if args.include_qids else set()

    parents: dict[str, list[str]] = {}
    candidates: dict[str, dict[str, Any]] = {}
    for count, entity in enumerate(open_dump(args.input), start=1):
        if args.limit and count > args.limit:
            break
        qid = entity.get("id", "")
        if not qid.startswith("Q"):
            continue
        p279 = claim_values(entity, "P279")
        if p279:
            parents[qid] = p279
        p31 = claim_values(entity, "P31")
        sitelinks = len(entity.get("sitelinks", {}))
        if sitelinks < args.min_sitelinks and qid not in forced:
            continue
        candidates[qid] = {"entity": entity, "p31": p31, "sitelinks": sitelinks}
        if count % 1_000_000 == 0:
            log.info(f"scanned {count} entities, candidates={len(candidates)}")

    org_classes = subclass_closure(ORG_ROOTS, parents)
    ent_rows: list[dict[str, Any]] = []
    alias_rows: list[dict[str, Any]] = []
    claim_rows: list[dict[str, Any]] = []
    for qid, item in candidates.items():
        kind = classify(item["p31"], org_classes)
        if kind is None:
            continue
        entity = item["entity"]
        labels = {
            lg: entity["labels"][lg]["value"] for lg in LANGS if lg in entity.get("labels", {})
        }
        descs = {
            lg: entity["descriptions"][lg]["value"]
            for lg in LANGS
            if lg in entity.get("descriptions", {})
        }
        ent_rows.append(
            {
                "qid": qid,
                "kind": kind,
                "sitelinks": item["sitelinks"],
                "labels": json.dumps(labels, ensure_ascii=False),
                "descriptions": json.dumps(descs, ensure_ascii=False),
            }
        )
        for lg in LANGS:
            for alias in entity.get("aliases", {}).get(lg, []):
                alias_rows.append({"qid": qid, "lang": lg, "alias": alias["value"]})
        for prop in PROPERTIES:
            for value in claim_values(entity, prop):
                claim_rows.append({"qid": qid, "property": prop, "value": value})

    out_dir = PROCESSED_DIR / "wikidata"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("entities", ent_rows), ("aliases", alias_rows), ("claims", claim_rows)):
        path = out_dir / f"{name}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path)
        record_file(SOURCE_KEY, path)
        log.info(f"{name}: {len(rows)} rows -> {path}")


if __name__ == "__main__":
    main()
