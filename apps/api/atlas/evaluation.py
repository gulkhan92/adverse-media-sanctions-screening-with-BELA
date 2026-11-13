"""Retrieval evaluation for the vector index (phase 2 exit criterion).

Three query sets, each of 1,000 names with a fixed seed:
  perturbed   watchlist names with typos, swapped characters, reversed token order;
              expected result: the same watchlist entry.
  cross_script  watchlist aliases written in a different script than the primary name,
              expected result: the owning entry (pairs such as Arabic or Cyrillic versus Latin).
  multilingual  non-English Wikidata labels; expected result: the same knowledge-base entity.
Ranks are computed over owners (an entry or entity with several vectors counts once).
"""

from __future__ import annotations

import random
import statistics
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from atlas.embeddings import KB_PREDICATE, WATCHLIST_PREDICATE, Embedder, search
from atlas.names import script_of

TARGETS = {
    "perturbed": {"recall@1": 0.90, "recall@5": 0.97},
    "cross_script": {"recall@1": 0.50, "recall@5": 0.80},
    "multilingual": {"recall@1": 0.60, "recall@5": 0.85},
}


@dataclass
class QuerySet:
    name: str
    queries: list[tuple[str, int, int | None]]  # (text, expected owner id, own alias id)
    predicate: str
    note: str


def perturb(name: str, rng: random.Random) -> str:
    mode = rng.choice(["drop", "swap", "reverse", "case"])
    chars = list(name)
    if mode == "drop" and len(chars) > 4:
        del chars[rng.randrange(1, len(chars) - 1)]
    elif mode == "swap" and len(chars) > 4:
        i = rng.randrange(1, len(chars) - 2)
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
    elif mode == "reverse":
        return " ".join(reversed(name.split()))
    elif mode == "case":
        return name.upper() if name != name.upper() else name.lower()
    return "".join(chars)


def build_query_sets(
    conn: psycopg.Connection[Any], size: int = 1000, seed: int = 42
) -> list[QuerySet]:
    rng = random.Random(seed)
    sets: list[QuerySet] = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT e.id, e.name FROM watchlist_entries e "
            "WHERE e.kind IN ('person','organization') AND char_length(e.name) >= 8 "
            "AND EXISTS (SELECT 1 FROM entity_embeddings m WHERE m.ref_type = 'watchlist_entry' "
            "AND m.ref_id = e.id) ORDER BY e.id"
        )
        rows = cur.fetchall()
        sample = rng.sample(rows, min(size, len(rows)))
        sets.append(
            QuerySet(
                "perturbed",
                [(perturb(n, rng), i, None) for i, n in sample],
                WATCHLIST_PREDICATE,
                "typos, swaps, reversed tokens, case",
            )
        )

        cur.execute(
            "SELECT a.entry_id, a.alias, e.name, a.id FROM watchlist_aliases a "
            "JOIN watchlist_entries e ON e.id = a.entry_id "
            "WHERE a.script <> 'LATIN' AND a.script <> 'NONE' ORDER BY a.id"
        )
        cross = [
            (i, alias, aid) for i, alias, name, aid in cur.fetchall() if script_of(name) == "LATIN"
        ]
        cross = rng.sample(cross, min(size, len(cross)))
        sets.append(
            QuerySet(
                "cross_script",
                [(alias, i, aid) for i, alias, aid in cross],
                WATCHLIST_PREDICATE,
                "non-Latin alias queried against the Latin primary name",
            )
        )

        cur.execute(
            "SELECT a.entity_id, a.alias, a.id FROM entity_aliases a "
            "WHERE a.source = 'wikidata' AND a.lang IN ('es','fr','de','ar','ru','tr') "
            "AND NOT a.is_primary AND EXISTS (SELECT 1 FROM entity_embeddings m WHERE "
            "m.ref_type = 'entity_alias' AND m.ref_id = a.id) ORDER BY a.id"
        )
        multi = cur.fetchall()
        multi = rng.sample(multi, min(size, len(multi)))
        sets.append(
            QuerySet(
                "multilingual",
                [(alias, i, aid) for i, alias, aid in multi],
                KB_PREDICATE,
                "non-English Wikidata alias against the knowledge base",
            )
        )
    return sets


def evaluate_set(
    conn: psycopg.Connection[Any], embedder: Embedder, qs: QuerySet, k: int = 20
) -> dict[str, Any]:
    register_vector(conn)
    if not qs.queries:
        return {"name": qs.name, "n": 0}
    vectors = embedder.encode([q for q, _, _ in qs.queries])
    ranks: list[int | None] = []
    latencies: list[float] = []
    for vector, (_, expected, own) in zip(vectors, qs.queries, strict=True):
        started = time.perf_counter()
        hits = search(
            conn, np.asarray(vector), qs.predicate, k=k, model_version=embedder.model_version
        )
        latencies.append((time.perf_counter() - started) * 1000)
        owners: list[int] = []
        for ref_type, ref_id, owner, _ in hits:
            # The query alias is itself indexed; its own vector must not count as a hit.
            if own is not None and ref_type.endswith("_alias") and ref_id == own:
                continue
            if owner not in owners:
                owners.append(owner)
        ranks.append(owners.index(expected) + 1 if expected in owners else None)
    n = len(ranks)
    result: dict[str, Any] = {
        "name": qs.name,
        "n": n,
        "note": qs.note,
        "recall@1": sum(1 for r in ranks if r == 1) / n,
        "recall@5": sum(1 for r in ranks if r is not None and r <= 5) / n,
        "recall@10": sum(1 for r in ranks if r is not None and r <= 10) / n,
        "latency_p50_ms": statistics.median(latencies),
        "latency_p95_ms": float(np.percentile(latencies, 95)),
    }
    target = TARGETS[qs.name]
    result["targets"] = target
    result["passed"] = all(result[m] >= v for m, v in target.items())
    return result
