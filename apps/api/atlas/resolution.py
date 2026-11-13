"""Cross-source resolution: link watchlist entries to knowledge-base entities.

Methods, in order of precedence (a stronger method is never overwritten by a weaker one):
  1. identifier   LEI found in the watchlist identifiers equals an entity LEI. Score 1.0.
  2. name_country exact normalized name or alias match with agreeing attributes
                  (country overlap, or birth year match for persons).
  3. name         exact normalized match without confirming attributes. Goes to review.
  4. embedding    bge-m3 cosine similarity over names for entries still unlinked. Medium
                  confidence goes to the review queue.
Every link stores its method, score, and status (accepted, review, rejected).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from atlas.config import EMBEDDING_MODEL
from atlas.embeddings import KB_PREDICATE, search

EMBED_REVIEW_MIN = 0.85
EMBED_ACCEPT_MIN = 0.97
MIN_NAME_LENGTH = 4

IDENTIFIER_SQL = """
INSERT INTO watchlist_entity_links (entry_id, entity_id, method, score, status)
SELECT DISTINCT e.id, en.id, 'identifier', 1.0, 'accepted'
FROM watchlist_entries e
CROSS JOIN LATERAL jsonb_array_elements(e.identifiers) AS i
JOIN entities en ON en.lei = i ->> 'value'
WHERE i ->> 'type' ILIKE '%legal entity%' OR i ->> 'type' ILIKE '%LEI%'
ON CONFLICT (entry_id, entity_id) DO NOTHING
"""

# Candidate pairs by exact normalized name or alias, then attribute checks.
NAME_SQL = """
WITH wl_names AS (
    SELECT id AS entry_id, kind, name_norm AS norm FROM watchlist_entries
    WHERE kind IN ('person', 'organization') AND char_length(name_norm) >= %(min_len)s
    UNION
    SELECT a.entry_id, e.kind, a.alias_norm FROM watchlist_aliases a
    JOIN watchlist_entries e ON e.id = a.entry_id
    WHERE e.kind IN ('person', 'organization') AND NOT a.low_quality
      AND char_length(a.alias_norm) >= %(min_len)s
), kb_names AS (
    SELECT id AS entity_id, kind, name_norm AS norm FROM entities
    UNION
    SELECT a.entity_id, e.kind, a.alias_norm FROM entity_aliases a
    JOIN entities e ON e.id = a.entity_id
), pairs AS (
    SELECT w.entry_id, k.entity_id,
           count(*) AS name_hits,
           bool_or(w.norm IN (SELECT name_norm FROM watchlist_entries x
                              WHERE x.id = w.entry_id)) AS primary_hit
    FROM wl_names w JOIN kb_names k ON k.norm = w.norm AND k.kind = w.kind
    GROUP BY w.entry_id, k.entity_id
), scored AS (
    SELECT p.entry_id, p.entity_id, p.primary_hit,
           e.kind,
           (cardinality(e.countries) > 0 AND cardinality(n.countries) > 0) AS both_countries,
           (e.countries && n.countries) AS country_overlap,
           (e.kind = 'person' AND cardinality(e.birth_years) > 0 AND n.birth_date IS NOT NULL) AS
               both_dob,
           (e.kind = 'person' AND n.birth_date IS NOT NULL
               AND left(n.birth_date, 4)::int = ANY (e.birth_years)) AS dob_match
    FROM pairs p
    JOIN watchlist_entries e ON e.id = p.entry_id
    JOIN entities n ON n.id = p.entity_id
), decided AS (
    SELECT entry_id, entity_id,
        CASE
            WHEN both_dob AND NOT dob_match THEN 'name'
            WHEN dob_match AND (country_overlap OR NOT both_countries) THEN 'name_country'
            WHEN both_countries AND country_overlap THEN 'name_country'
            ELSE 'name'
        END AS method,
        CASE
            WHEN both_dob AND NOT dob_match THEN 0.30
            WHEN dob_match AND country_overlap THEN 0.98
            WHEN dob_match THEN 0.93
            WHEN both_countries AND country_overlap THEN 0.95
            WHEN both_countries THEN 0.40
            WHEN primary_hit THEN 0.75
            ELSE 0.65
        END AS score
    FROM scored
), ranked AS (
    SELECT d.*, count(*) FILTER (WHERE score >= 0.90) OVER (PARTITION BY entry_id) AS strong_peers
    FROM decided d
)
INSERT INTO watchlist_entity_links (entry_id, entity_id, method, score, status)
SELECT entry_id, entity_id, method,
       CASE WHEN score >= 0.90 AND strong_peers > 1 THEN score * 0.8 ELSE score END,
       CASE
           WHEN score < 0.50 THEN 'rejected'
           WHEN score >= 0.90 AND strong_peers = 1 THEN 'accepted'
           ELSE 'review'
       END
FROM ranked
ON CONFLICT (entry_id, entity_id) DO NOTHING
"""


def link_by_identifier(conn: psycopg.Connection[Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(IDENTIFIER_SQL)
        return cur.rowcount


def link_by_name(conn: psycopg.Connection[Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(NAME_SQL, {"min_len": MIN_NAME_LENGTH})
        return cur.rowcount


def link_by_embedding(
    conn: psycopg.Connection[Any], model_version: str = EMBEDDING_MODEL, ef_search: int = 100
) -> dict[str, int]:
    """Nearest knowledge-base neighbour for entries that have no accepted or review link."""
    register_vector(conn)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT em.owner_id, e.kind, e.countries, em.embedding
               FROM entity_embeddings em JOIN watchlist_entries e ON e.id = em.owner_id
               WHERE em.ref_type = 'watchlist_entry' AND em.model_version = %s
                 AND e.kind IN ('person', 'organization')
                 AND NOT EXISTS (SELECT 1 FROM watchlist_entity_links l
                                 WHERE l.entry_id = e.id AND l.status IN ('accepted', 'review'))""",
            (model_version,),
        )
        todo = cur.fetchall()
    counts = {"queried": len(todo), "review": 0, "accepted": 0}
    for entry_id, kind, countries, vector in todo:
        hits = search(
            conn,
            vector.to_numpy() if hasattr(vector, "to_numpy") else np.asarray(vector),
            KB_PREDICATE,
            k=10,
            model_version=model_version,
            ef_search=ef_search,
        )
        if not hits:
            continue
        owner_ids = [h[2] for h in hits]
        with conn.cursor() as cur:
            cur.execute("SELECT id, kind, countries FROM entities WHERE id = ANY(%s)", (owner_ids,))
            info = {row[0]: row for row in cur.fetchall()}
        for _, _, owner_id, similarity in hits:
            _, ekind, ecountries = info[owner_id]
            if ekind != kind or similarity < EMBED_REVIEW_MIN:
                continue
            overlap = bool(set(countries) & set(ecountries))
            conflict = bool(countries and ecountries and not overlap)
            status = "accepted" if similarity >= EMBED_ACCEPT_MIN and not conflict else "review"
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO watchlist_entity_links
                       (entry_id, entity_id, method, score, status)
                       VALUES (%s, %s, 'embedding', %s, %s) ON CONFLICT (entry_id, entity_id)
                       DO NOTHING""",
                    (entry_id, owner_id, similarity, status),
                )
            counts[status] += 1
            break
    conn.commit()
    return counts


def coverage(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM watchlist_entries WHERE kind IN ('person','organization')"
        )
        eligible = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "SELECT status, method, count(*), count(DISTINCT entry_id) "
            "FROM watchlist_entity_links GROUP BY 1, 2 ORDER BY 1, 2"
        )
        by_method = [
            {"status": s, "method": m, "links": n, "entries": d} for s, m, n, d in cur.fetchall()
        ]
        cur.execute(
            "SELECT count(DISTINCT entry_id) FILTER (WHERE status = 'accepted'), "
            "count(DISTINCT entry_id) FILTER (WHERE status IN ('accepted','review')) "
            "FROM watchlist_entity_links"
        )
        accepted, any_link = cur.fetchone()  # type: ignore[misc]
        cur.execute(
            "SELECT count(DISTINCT l.entry_id) FROM watchlist_entity_links l "
            "JOIN entities e ON e.id = l.entity_id WHERE l.status = 'accepted' "
            "AND e.qid IS NOT NULL"
        )
        with_qid = cur.fetchone()[0]  # type: ignore[index]
    return {
        "eligible_entries": eligible,
        "accepted_entries": accepted,
        "linked_or_review": any_link,
        "accepted_with_qid": with_qid,
        "by_method": by_method,
        "accepted_pct": round(100 * accepted / eligible, 2) if eligible else 0.0,
        "linked_or_review_pct": round(100 * any_link / eligible, 2) if eligible else 0.0,
    }


def resolve(conn: psycopg.Connection[Any], use_embeddings: bool = True) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM watchlist_entity_links")
    result: dict[str, Any] = {"identifier": link_by_identifier(conn)}
    result["name"] = link_by_name(conn)
    conn.commit()
    if use_embeddings:
        result["embedding"] = link_by_embedding(conn)
    result["coverage"] = coverage(conn)
    return result
