"""Name embeddings: bge-m3 encoding, storage in pgvector, and similarity search.

Embedded text is the original-script name, so cross-script variants (Latin, Cyrillic, Arabic)
land close together in the multilingual space. Rows are stored in ``entity_embeddings`` with
the model version; a different model writes a different set of rows.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import time
from collections.abc import Iterator, Sequence
from typing import Any, Protocol

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from atlas.config import EMBEDDING_DIM, EMBEDDING_MODEL
from atlas.db import connect, copy_rows

WATCHLIST_REFS = ("watchlist_entry", "watchlist_alias")
KB_REFS = ("entity", "entity_alias")
# Exact predicates of the partial HNSW indexes (migration 0003).
WATCHLIST_PREDICATE = "ref_type IN ('watchlist_entry', 'watchlist_alias')"
KB_PREDICATE = "ref_type IN ('entity', 'entity_alias')"
HNSW_INDEXES = {
    "ix_entity_embeddings_hnsw_watchlist": WATCHLIST_PREDICATE,
    "ix_entity_embeddings_hnsw_kb": KB_PREDICATE,
}


class Embedder(Protocol):
    model_version: str

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class BgeM3Embedder:
    """BAAI/bge-m3 (MIT, 1024 dimensions) through sentence-transformers."""

    def __init__(self, model_name: str = EMBEDDING_MODEL, max_seq_length: int = 48) -> None:
        import torch
        from sentence_transformers import SentenceTransformer

        device = (
            "cuda"
            if torch.cuda.is_available()
            else ("mps" if torch.backends.mps.is_available() else "cpu")
        )
        self.model = SentenceTransformer(model_name, device=device)
        self.model.max_seq_length = max_seq_length  # names are short; cap for speed
        if device != "cpu":
            self.model.half()
        self.model_version = model_name
        self.device = device

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self.model.encode(
            list(texts),
            batch_size=256,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


class HashingEmbedder:
    """Deterministic character n-gram hashing embedder (no model download).

    Used for tests and CPU-only smoke runs. It captures spelling similarity within a script
    but not cross-script similarity, and must not be used for reported results.
    """

    model_version = "char-ngram-hash-v1"

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            collapsed = re.sub(r"\s+", " ", text.casefold())
            padded = f"  {collapsed}  "
            for n in (2, 3, 4):
                for i in range(len(padded) - n + 1):
                    digest = hashlib.blake2b(padded[i : i + n].encode(), digest_size=4).digest()
                    out[row, int.from_bytes(digest, "little") % EMBEDDING_DIM] += 1.0
            norm = np.linalg.norm(out[row])
            if norm:
                out[row] /= norm
        return out


def make_embedder(kind: str) -> Embedder:
    return HashingEmbedder() if kind == "hash" else BgeM3Embedder()


# Source queries return (ref_type, ref_id, owner_id, display_text, name_norm) for rows
# that do not have an embedding for the model version yet.
SOURCES: dict[str, str] = {
    "watchlist_entry": """
        SELECT 'watchlist_entry', e.id, e.id, e.name, e.name_norm FROM watchlist_entries e
        WHERE NOT EXISTS (SELECT 1 FROM entity_embeddings x WHERE x.ref_type = 'watchlist_entry'
                          AND x.ref_id = e.id AND x.model_version = %(mv)s)""",
    "watchlist_alias": """
        SELECT 'watchlist_alias', a.id, a.entry_id, a.alias, a.alias_norm
        FROM watchlist_aliases a
        WHERE NOT EXISTS (SELECT 1 FROM entity_embeddings x WHERE x.ref_type = 'watchlist_alias'
                          AND x.ref_id = a.id AND x.model_version = %(mv)s)""",
    "entity": """
        SELECT 'entity', e.id, e.id, e.name, e.name_norm FROM entities e
        WHERE NOT EXISTS (SELECT 1 FROM entity_embeddings x WHERE x.ref_type = 'entity'
                          AND x.ref_id = e.id AND x.model_version = %(mv)s)""",
    # Aliases are embedded for the Wikidata source only (multilingual labels and aliases).
    "entity_alias": """
        SELECT 'entity_alias', a.id, a.entity_id, a.alias, a.alias_norm FROM entity_aliases a
        WHERE a.source = 'wikidata' AND NOT a.is_primary
          AND NOT EXISTS (SELECT 1 FROM entity_embeddings x WHERE x.ref_type = 'entity_alias'
                          AND x.ref_id = a.id AND x.model_version = %(mv)s)""",
}


def drop_indexes(conn: psycopg.Connection[Any]) -> None:
    for name in HNSW_INDEXES:
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    conn.commit()


def create_indexes(conn: psycopg.Connection[Any]) -> None:
    for name, predicate in HNSW_INDEXES.items():
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {name} ON entity_embeddings USING hnsw "
            f"(embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64) WHERE {predicate}"
        )
    conn.commit()


def batches(rows: list[tuple[Any, ...]], size: int) -> Iterator[list[tuple[Any, ...]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def embed_missing(
    conn: psycopg.Connection[Any],
    embedder: Embedder,
    ref_type: str,
    limit: int = 0,
    chunk: int = 4096,
    log: bool = True,
) -> int:
    with conn.cursor() as cur:
        cur.execute(SOURCES[ref_type], {"mv": embedder.model_version})
        rows = cur.fetchall()
    if limit:
        rows = rows[:limit]
    done, started = 0, time.time()
    for part in batches(rows, chunk):
        unique = sorted({r[3] for r in part})
        vectors = dict(zip(unique, embedder.encode(unique), strict=True))
        copy_rows(
            conn,
            "entity_embeddings",
            ["ref_type", "ref_id", "owner_id", "name_norm", "embedding", "model_version"],
            ((r[0], r[1], r[2], r[4], vectors[r[3]], embedder.model_version) for r in part),
        )
        conn.commit()
        done += len(part)
        if log:
            rate = done / max(time.time() - started, 1e-6)
            print(f"{ref_type}: {done}/{len(rows)} ({rate:.0f}/s)", flush=True)
    return done


def search(
    conn: psycopg.Connection[Any],
    vector: np.ndarray,
    predicate: str,
    k: int = 10,
    model_version: str = EMBEDDING_MODEL,
    ef_search: int = 100,
) -> list[tuple[str, int, int, float]]:
    """Nearest names by cosine similarity. ``predicate`` must match an index predicate."""
    with conn.cursor() as cur:
        cur.execute(f"SET LOCAL hnsw.ef_search = {int(ef_search)}")
        cur.execute(
            f"SELECT ref_type, ref_id, owner_id, 1 - (embedding <=> %s) AS similarity "
            f"FROM entity_embeddings WHERE {predicate} AND model_version = %s "
            f"ORDER BY embedding <=> %s LIMIT %s",
            (vector, model_version, vector, k),
        )
        return [(a, b, c, float(d)) for a, b, c, d in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["bge-m3", "hash"], default="bge-m3")
    parser.add_argument("--refs", nargs="+", default=list(SOURCES), choices=list(SOURCES))
    parser.add_argument("--limit", type=int, default=0, help="rows per ref type (testing)")
    parser.add_argument(
        "--keep-indexes",
        action="store_true",
        help="do not drop and rebuild the HNSW indexes around the load",
    )
    args = parser.parse_args()
    embedder = make_embedder("hash" if args.model == "hash" else "bge")
    with connect() as conn:
        register_vector(conn)
        if not args.keep_indexes:
            drop_indexes(conn)
        for ref in args.refs:
            embed_missing(conn, embedder, ref, args.limit)
        if not args.keep_indexes:
            started = time.time()
            create_indexes(conn)
            print(f"hnsw indexes built in {time.time() - started:.0f}s")
        conn.autocommit = True
        conn.execute("ANALYZE entity_embeddings")


if __name__ == "__main__":
    main()
