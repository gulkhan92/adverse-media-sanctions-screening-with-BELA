"""Fetch a targeted Wikidata subset through public APIs (no full dump required).

Sub-commands:
  leis       Wikidata items that carry a LEI (P1278), via the SPARQL endpoint.
  search     Search Wikidata for every OFAC primary name (resumable cache).
  countries  Mapping from country QIDs to ISO 3166-1 alpha-2 codes (P297).
  fetch      Download labels, descriptions, aliases, claims, and sitelink counts for the
             QIDs collected by the commands above and write the same three parquet files
             as build_wikidata_subset.py (entities, aliases, claims).

This complements build_wikidata_subset.py. The dump-based builder is the way to reach the
planned 500 thousand entities; the API-based subset covers watchlist-linked and LEI-linked
entities and is what phase 2 loads. Licence: CC0. Requests follow the Wikimedia API
etiquette (identifying user agent, low concurrency, retry on throttling).
"""

from __future__ import annotations

import argparse
import csv
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from build_wikidata_subset import LANGS, PROPERTIES, claim_values
from common import PROCESSED_DIR, RAW_DIR, USER_AGENT, get_logger, record_file

SOURCE_KEY = "wikidata"
OUT = PROCESSED_DIR / "wikidata"
SPARQL = "https://query.wikidata.org/sparql"
API = "https://www.wikidata.org/w/api.php"
# Properties that identify an item as an organization when it is not a human.
ORG_PROPERTIES = ("P1278", "P159", "P452", "P112", "P740", "P355", "P749", "P127", "P571")
log = get_logger("wikidata_fetch")


def client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)


def get_json(http: httpx.Client, url: str, params: dict[str, str], retries: int = 6) -> Any:
    for attempt in range(retries):
        response = http.get(url, params=params)
        if response.status_code in (429, 502, 503, 504):
            time.sleep(min(60, 2 ** (attempt + 1)))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError(f"gave up on {url} after {retries} attempts")


def sparql(http: httpx.Client, query: str) -> list[dict[str, Any]]:
    data = get_json(http, SPARQL, {"query": query, "format": "json"})
    return list(data["results"]["bindings"])


def cmd_leis() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str]] = []
    with client() as http:
        step, offset = 20000, 0
        while True:
            query = (
                "SELECT ?q ?lei WHERE { ?q wdt:P1278 ?lei } "
                f"ORDER BY ?q LIMIT {step} OFFSET {offset}"
            )
            batch = sparql(http, query)
            rows += [(r["q"]["value"].rsplit("/", 1)[1], r["lei"]["value"]) for r in batch]
            log.info(f"lei links so far: {len(rows)}")
            if len(batch) < step:
                break
            offset += step
    (OUT / "lei_links.json").write_text(json.dumps(rows))


def cmd_countries() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with client() as http:
        rows = sparql(http, "SELECT ?c ?iso WHERE { ?c wdt:P297 ?iso }")
    mapping = {r["c"]["value"].rsplit("/", 1)[1]: r["iso"]["value"] for r in rows}
    (OUT / "countries.json").write_text(json.dumps(mapping))
    log.info(f"country mappings: {len(mapping)}")


def ofac_names() -> list[str]:
    path = RAW_DIR / "sanctions" / "ofac" / "SDN.CSV"
    names: list[str] = []
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) < 3 or not row[0].strip().isdigit():
                continue
            kind = row[2].strip().lower()
            if kind in ("vessel", "aircraft"):
                continue
            name = row[1].strip()
            if kind == "individual" and ", " in name:
                last, first = name.split(", ", 1)
                name = f"{first} {last}"
            names.append(name)
    return names


_local = threading.local()
REQUEST_DELAY_SECONDS = 0.9  # per worker; keeps the aggregate rate near 2 requests per second


def search_one(name: str) -> tuple[str, list[str]]:
    params = {
        "action": "wbsearchentities",
        "search": name,
        "language": "en",
        "uselang": "en",
        "type": "item",
        "limit": "5",
        "format": "json",
    }
    if not hasattr(_local, "http"):
        _local.http = client()
    time.sleep(REQUEST_DELAY_SECONDS)
    try:
        data = get_json(_local.http, API, params, retries=8)
    except (httpx.HTTPError, RuntimeError):
        return name, ["__error__"]
    return name, [hit["id"] for hit in data.get("search", [])]


def cmd_search(workers: int, limit: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cache_path = OUT / "search_cache.jsonl"
    done: set[str] = set()
    if cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["qids"] != ["__error__"]:
                done.add(record["name"])
    todo = [n for n in dict.fromkeys(ofac_names()) if n not in done]
    if limit:
        todo = todo[:limit]
    log.info(f"searching {len(todo)} names ({len(done)} cached)")
    with cache_path.open("a", encoding="utf-8") as sink, ThreadPoolExecutor(workers) as pool:
        for count, (name, qids) in enumerate(pool.map(search_one, todo), start=1):
            sink.write(json.dumps({"name": name, "qids": qids}, ensure_ascii=False) + "\n")
            if count % 50 == 0:
                sink.flush()
            if count % 500 == 0:
                log.info(f"searched {count}/{len(todo)}")


def collect_qids() -> set[str]:
    qids: set[str] = set()
    leis = OUT / "lei_links.json"
    if leis.exists():
        qids |= {q for q, _ in json.loads(leis.read_text())}
    cache = OUT / "search_cache.jsonl"
    if cache.exists():
        for line in cache.read_text(encoding="utf-8").splitlines():
            qids |= {q for q in json.loads(line)["qids"] if q.startswith("Q")}
    return qids


def classify_entity(entity: dict[str, Any]) -> str | None:
    p31 = claim_values(entity, "P31")
    if "Q5" in p31:
        return "person"
    claims = entity.get("claims", {})
    if any(prop in claims for prop in ORG_PROPERTIES):
        return "organization"
    return None


def fetch_batch(batch: list[str]) -> list[dict[str, Any]]:
    params = {
        "action": "wbgetentities",
        "ids": "|".join(batch),
        "format": "json",
        "props": "labels|descriptions|aliases|claims|sitelinks",
        "languages": "|".join(LANGS),
    }
    with client() as http:
        data = get_json(http, API, params)
    return [e for e in data.get("entities", {}).values() if "missing" not in e]


def trim_entity(entity: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce an API entity to the fields the loader needs. None when not a person/org."""
    kind = classify_entity(entity)
    labels = {lg: entity["labels"][lg]["value"] for lg in LANGS if lg in entity.get("labels", {})}
    if kind is None or not labels:
        return None
    return {
        "qid": entity["id"],
        "kind": kind,
        "sitelinks": len(entity.get("sitelinks", {})),
        "labels": labels,
        "descriptions": {
            lg: entity["descriptions"][lg]["value"]
            for lg in LANGS
            if lg in entity.get("descriptions", {})
        },
        "aliases": {
            lg: [a["value"] for a in entity.get("aliases", {}).get(lg, [])] for lg in LANGS
        },
        "claims": {prop: claim_values(entity, prop) for prop in PROPERTIES},
    }


def cmd_fetch(workers: int, from_cache: bool = False) -> None:
    """Fetch missing entities into entity_cache.jsonl, then write the parquet files."""
    OUT.mkdir(parents=True, exist_ok=True)
    cache_path = OUT / "entity_cache.jsonl"
    cached: dict[str, dict[str, Any] | None] = {}
    if cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            cached[record["qid"]] = record.get("entity")
    qids = [] if from_cache else sorted(q for q in collect_qids() if q not in cached)
    batches = [qids[i : i + 50] for i in range(0, len(qids), 50)]
    log.info(f"fetching {len(qids)} new entities in {len(batches)} batches ({len(cached)} cached)")
    with cache_path.open("a", encoding="utf-8") as sink, ThreadPoolExecutor(workers) as pool:
        for number, (batch, entities) in enumerate(
            zip(batches, pool.map(fetch_batch, batches), strict=True), start=1
        ):
            returned = {e["id"]: e for e in entities}
            for qid in batch:
                trimmed = trim_entity(returned[qid]) if qid in returned else None
                cached[qid] = trimmed
                sink.write(json.dumps({"qid": qid, "entity": trimmed}, ensure_ascii=False) + "\n")
            if number % 50 == 0:
                sink.flush()
                log.info(f"fetched {number}/{len(batches)} batches")
    wanted = collect_qids()
    ent_rows: list[dict[str, Any]] = []
    alias_rows: list[dict[str, Any]] = []
    claim_rows: list[dict[str, Any]] = []
    for qid in sorted(wanted):
        item = cached.get(qid)
        if not item:
            continue
        ent_rows.append(
            {
                "qid": qid,
                "kind": item["kind"],
                "sitelinks": item["sitelinks"],
                "labels": json.dumps(item["labels"], ensure_ascii=False),
                "descriptions": json.dumps(item["descriptions"], ensure_ascii=False),
            }
        )
        for lg, values in item["aliases"].items():
            alias_rows += [{"qid": qid, "lang": lg, "alias": v} for v in values]
        for prop, values in item["claims"].items():
            claim_rows += [{"qid": qid, "property": prop, "value": v} for v in values]
    for name, rows in (("entities", ent_rows), ("aliases", alias_rows), ("claims", claim_rows)):
        path = OUT / f"{name}.parquet"
        pq.write_table(pa.Table.from_pylist(rows), path)
        record_file(SOURCE_KEY, path)
        log.info(f"{name}: {len(rows)} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("leis")
    sub.add_parser("countries")
    search = sub.add_parser("search")
    search.add_argument("--workers", type=int, default=2)
    search.add_argument("--limit", type=int, default=0)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--workers", type=int, default=1)
    fetch.add_argument("--from-cache", action="store_true", help="build parquet, no network")
    args = parser.parse_args()
    if args.command == "leis":
        cmd_leis()
    elif args.command == "countries":
        cmd_countries()
    elif args.command == "search":
        cmd_search(args.workers, args.limit)
    else:
        cmd_fetch(args.workers, args.from_cache)


if __name__ == "__main__":
    main()
