"""Screening of one subject: retrieval, feature scoring, risk score, and stored explanation.

Retrieval order (PLAN.md phase 5, task 1):
  1. exact identifiers (LEI, passport, registration numbers) against watchlist identifiers
  2. trigram similarity over normalized names, aliases, and the transliterated sorted-token key
  3. vector similarity over name embeddings (when an embedder is available)
Attribute checks (country, birth year) adjust the name confidence. Linked adverse media
mentions are then retrieved and the score is computed by ``scoring.compute_score``.
Results are leads for human review.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from atlas.api.db import Row
from atlas.api.schemas import RiskConfig
from atlas.api.scoring import compute_score
from atlas.names import normalize_name

DEFAULT_CONFIG: dict[str, Any] = RiskConfig().model_dump()
CONFIG_VERSION = "risk-config-v2"
VECTOR_MIN = 0.80
VECTOR_PENALTY = 0.93  # embeddings are a weaker signal than exact or trigram name agreement


@dataclass
class Embedder:
    """Name embedder used for vector retrieval. ``embed`` returns None when unavailable."""

    model_version: str
    embed: Callable[[str], np.ndarray | None]


CANDIDATE_SQL = """
SELECT DISTINCT ON (entry_id) entry_id, name, kind, countries, birth_years, programs,
       source_code, list_type, sim, via
FROM (
    SELECT e.id AS entry_id, e.name, e.kind, e.countries, e.birth_years, e.programs,
           s.code AS source_code, s.list_type, similarity(e.name_norm, %(norm)s) AS sim,
           'name' AS via
    FROM watchlist_entries e JOIN watchlist_sources s ON s.id = e.source_id
    WHERE e.kind = %(kind)s AND e.name_norm %% %(norm)s
    UNION ALL
    SELECT e.id, e.name, e.kind, e.countries, e.birth_years, e.programs, s.code, s.list_type,
           similarity(a.alias_norm, %(norm)s), 'alias'
    FROM watchlist_aliases a JOIN watchlist_entries e ON e.id = a.entry_id
    JOIN watchlist_sources s ON s.id = e.source_id
    WHERE NOT a.low_quality AND e.kind = %(kind)s AND a.alias_norm %% %(norm)s
    UNION ALL
    SELECT e.id, e.name, e.kind, e.countries, e.birth_years, e.programs, s.code, s.list_type,
           similarity(e.name_key, %(key)s) * 0.97, 'transliteration'
    FROM watchlist_entries e JOIN watchlist_sources s ON s.id = e.source_id
    WHERE e.kind = %(kind)s AND %(key)s <> '' AND e.name_key %% %(key)s
) c
ORDER BY entry_id, sim DESC
"""

ENTRY_SQL = (
    "SELECT e.id AS entry_id, e.name, e.kind, e.countries, e.birth_years, e.programs, "
    "s.code AS source_code, s.list_type FROM watchlist_entries e "
    "JOIN watchlist_sources s ON s.id = e.source_id WHERE e.id = ANY(%s)"
)


def load_config(conn: psycopg.Connection[Row], tenant_id: uuid.UUID) -> tuple[dict[str, Any], int]:
    row = conn.execute(
        "SELECT config, version FROM risk_config WHERE tenant_id = %s", (tenant_id,)
    ).fetchone()
    if row is None:
        return DEFAULT_CONFIG, 0
    try:
        merged = RiskConfig.model_validate({**DEFAULT_CONFIG, **row["config"]})
    except ValueError:
        return DEFAULT_CONFIG, int(row["version"])
    return merged.model_dump(), int(row["version"])


def score_candidate(
    cand: Row, country: str | None, dob_year: int | None
) -> tuple[float, dict[str, Any]]:
    confidence = float(cand["sim"])
    features: dict[str, Any] = {"name_similarity": round(confidence, 4), "via": cand["via"]}
    if country and cand["countries"]:
        if country in cand["countries"]:
            confidence += 0.08
            features["country"] = "match"
        else:
            confidence -= 0.10
            features["country"] = "conflict"
    else:
        features["country"] = "unknown"
    if dob_year and cand["birth_years"]:
        if dob_year in cand["birth_years"]:
            confidence += 0.12
            features["birth_year"] = "match"
        else:
            confidence -= 0.25
            features["birth_year"] = "conflict"
    else:
        features["birth_year"] = "unknown"
    return max(0.0, min(1.0, confidence)), features


def _match(cand: Row, confidence: float, features: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_type": "watchlist",
        "entry_id": cand["entry_id"],
        "name": cand["name"],
        "source": cand["source_code"],
        "list_type": cand["list_type"],
        "programs": cand["programs"],
        "countries": cand["countries"],
        "confidence": round(confidence, 4),
        "features": features,
    }


def identifier_candidates(
    conn: psycopg.Connection[Row], kind: str, identifiers: list[dict[str, str]]
) -> list[Row]:
    found: dict[int, Row] = {}
    for ident in identifiers:
        rows = conn.execute(
            ENTRY_SQL.replace("WHERE e.id = ANY(%s)", "WHERE e.identifiers @> %s::jsonb")
            + " AND e.kind = %s LIMIT 5",
            (json.dumps([{"value": ident["value"]}]), kind),
        ).fetchall()
        for row in rows:
            found[row["entry_id"]] = {**row, "via": "identifier", "sim": 1.0}
    return list(found.values())


def vector_candidates(
    conn: psycopg.Connection[Row], embedder: Embedder | None, name: str, kind: str
) -> tuple[list[Row], str]:
    """Nearest watchlist names by embedding. Returns (candidates, status)."""
    if embedder is None:
        return [], "not_configured"
    vector = embedder.embed(name)
    if vector is None:
        return [], "unavailable"
    register_vector(conn)
    conn.execute("SELECT set_config('hnsw.ef_search', '100', true)")
    hits = conn.execute(
        # The predicate must match the partial HNSW index exactly (migration 0003).
        "SELECT owner_id, 1 - (embedding <=> %s) AS similarity FROM entity_embeddings "
        "WHERE ref_type IN ('watchlist_entry', 'watchlist_alias') AND model_version = %s "
        "ORDER BY embedding <=> %s LIMIT 10",
        (vector, embedder.model_version, vector),
    ).fetchall()
    best: dict[int, float] = {}
    for hit in hits:
        if hit["similarity"] >= VECTOR_MIN:
            best[hit["owner_id"]] = max(best.get(hit["owner_id"], 0.0), float(hit["similarity"]))
    if not best:
        return [], "ok"
    rows = conn.execute(ENTRY_SQL + " AND e.kind = %s", (list(best), kind)).fetchall()
    return [
        {**r, "via": "embedding", "sim": best[r["entry_id"]] * VECTOR_PENALTY} for r in rows
    ], "ok"


def watchlist_matches(
    conn: psycopg.Connection[Row],
    subject: Row,
    dob: str | None,
    config: dict[str, Any],
    identifiers: list[dict[str, str]] | None = None,
    embedder: Embedder | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    norm = normalize_name(subject["name"], subject["kind"])
    conn.execute("SELECT set_config('pg_trgm.similarity_threshold', '0.45', true)")
    trigram = conn.execute(
        CANDIDATE_SQL, {"norm": norm.stripped, "key": norm.key, "kind": subject["kind"]}
    ).fetchall()
    exact = identifier_candidates(conn, subject["kind"], identifiers or [])
    vector, vector_status = vector_candidates(conn, embedder, subject["name"], subject["kind"])
    dob_year = int(dob[:4]) if dob else None
    best: dict[int, dict[str, Any]] = {}
    for cand in [*trigram, *vector, *exact]:
        if cand["via"] == "identifier":
            confidence, features = 1.0, {"name_similarity": None, "via": "identifier"}
        else:
            confidence, features = score_candidate(cand, subject["country"], dob_year)
        if confidence < config["name_match_min"]:
            continue
        match = _match(cand, confidence, features)
        current = best.get(cand["entry_id"])
        if current is None or match["confidence"] > current["confidence"]:
            best[cand["entry_id"]] = match
    matches = sorted(best.values(), key=lambda m: m["confidence"], reverse=True)
    return matches[:10], {"vector_search": vector_status}


ADVERSE_BASE = (
    "SELECT m.id AS mention_id, m.el_score, m.surface, m.entity_id, a.title, a.url, "
    "a.published_at, f.severity, f.category, f.evidence_sentence, a.domain "
    "FROM mentions m JOIN articles a ON a.id = m.article_id "
    "JOIN LATERAL (SELECT severity, category, evidence_sentence FROM adverse_findings "
    "WHERE mention_id = m.id ORDER BY severity DESC LIMIT 1) f ON true "
)


def _item(row: Row, link_confidence: float, name_confidence: float, via: str) -> dict[str, Any]:
    return {
        "mention_id": row["mention_id"],
        "severity": int(row["severity"]),
        "category": row["category"],
        "link_confidence": round(max(0.0, min(1.0, link_confidence)), 4),
        "confidence": round(name_confidence, 4),
        "published_at": row["published_at"],
        "via": via,
        "surface": row["surface"],
        "evidence": row["evidence_sentence"],
        "article": {
            "title": row["title"],
            "url": row["url"],
            "published_at": row["published_at"],
            "domain": row["domain"],
        },
    }


def adverse_items(
    conn: psycopg.Connection[Row], subject: Row, watch: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Adverse media mentions for a subject, through three routes of decreasing certainty."""
    items: dict[int, dict[str, Any]] = {}

    def add(item: dict[str, Any]) -> None:
        current = items.get(item["mention_id"])
        if current is None or item["link_confidence"] > current["link_confidence"]:
            items[item["mention_id"]] = item

    for match in watch[:3]:  # route 1: mentions linked to the entity of a matched list entry
        rows = conn.execute(
            ADVERSE_BASE  # nosec B608 - concatenates module constants only
            + "WHERE m.entity_id IN (SELECT entity_id FROM watchlist_entity_links "
            "WHERE entry_id = %s AND status = 'accepted') AND m.decision <> 'nil' "
            "ORDER BY f.severity DESC, a.published_at DESC LIMIT 5",
            (match["entry_id"],),
        ).fetchall()
        for row in rows:
            conf = float(row["el_score"] or 0.5) * match["confidence"]
            add(
                {
                    **_item(row, conf, match["confidence"], "watchlist_link"),
                    "entry_id": match["entry_id"],
                }
            )
    norm = normalize_name(subject["name"], subject["kind"]).stripped
    conn.execute("SELECT set_config('pg_trgm.similarity_threshold', '0.8', true)")
    rows = conn.execute(  # route 2: mentions linked to an entity with the subject's name
        ADVERSE_BASE + "JOIN entities en ON en.id = m.entity_id WHERE m.decision <> 'nil' "
        "AND en.kind = %s AND en.name_norm %% %s "
        "ORDER BY f.severity DESC, a.published_at DESC LIMIT 10",
        (subject["kind"], norm),
    ).fetchall()
    for row in rows:
        add(_item(row, float(row["el_score"] or 0.5), 0.9, "entity_name"))
    rows = conn.execute(  # route 3: unlinked or linked mentions whose surface matches the name
        ADVERSE_BASE + "WHERE lower(m.surface) %% %s ORDER BY f.severity DESC, "
        "a.published_at DESC LIMIT 10",
        (norm,),
    ).fetchall()
    for row in rows:
        add(_item(row, 0.7, 0.7, "surface"))  # unlinked mentions carry less certainty
    return sorted(items.values(), key=lambda i: i["severity"] * i["link_confidence"], reverse=True)


def find_or_create_subject(
    conn: psycopg.Connection[Row], tenant_id: uuid.UUID, data: dict[str, Any], key: str
) -> Row:
    norm = normalize_name(data["name"], data["kind"])
    existing = conn.execute(
        "SELECT id, name, kind, country, name_norm FROM subjects WHERE tenant_id = %s "
        "AND name_norm = %s AND kind = %s AND deleted_at IS NULL "
        "AND country IS NOT DISTINCT FROM %s LIMIT 1",
        (tenant_id, norm.stripped, data["kind"], data.get("country")),
    ).fetchone()
    if existing:
        return existing
    identifiers = data.get("identifiers") or []
    row = conn.execute(
        "INSERT INTO subjects (tenant_id, kind, name, name_norm, aliases, country, dob_enc, "
        "identifiers_enc) VALUES (%s, %s, %s, %s, %s, %s, "
        "CASE WHEN %s::text IS NULL THEN NULL ELSE pgp_sym_encrypt(%s::text, %s) END, "
        "CASE WHEN %s::text IS NULL THEN NULL ELSE pgp_sym_encrypt(%s::text, %s) END) "
        "RETURNING id, name, kind, country, name_norm",
        (
            tenant_id,
            data["kind"],
            data["name"],
            norm.stripped,
            data.get("aliases", []),
            data.get("country"),
            data.get("dob"),
            data.get("dob"),
            key,
            json.dumps(identifiers) if identifiers else None,
            json.dumps(identifiers) if identifiers else None,
            key,
        ),
    ).fetchone()
    assert row is not None
    return row


def subject_secrets(
    conn: psycopg.Connection[Row], subject_id: uuid.UUID, key: str
) -> tuple[str | None, list[dict[str, str]]]:
    row = conn.execute(
        "SELECT CASE WHEN dob_enc IS NULL THEN NULL ELSE pgp_sym_decrypt(dob_enc, %s) END AS dob, "
        "CASE WHEN identifiers_enc IS NULL THEN NULL "
        "ELSE pgp_sym_decrypt(identifiers_enc, %s) END AS ids FROM subjects WHERE id = %s",
        (key, key, subject_id),
    ).fetchone()
    if row is None:
        return None, []
    return row["dob"], json.loads(row["ids"]) if row["ids"] else []


def evaluate(
    conn: psycopg.Connection[Row],
    subject: Row,
    dob: str | None,
    config: dict[str, Any],
    identifiers: list[dict[str, str]] | None = None,
    embedder: Embedder | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Run retrieval and scoring without writing anything."""
    watch, notes = watchlist_matches(conn, subject, dob, config, identifiers, embedder)
    adverse = adverse_items(conn, subject, watch) if config["adverse_media_enabled"] else []
    best_list = watch[0] if watch else None
    breakdown = compute_score(config, best_list, adverse, now)
    return {"watch": watch, "adverse": adverse, "breakdown": breakdown, "notes": notes}


def run_screening(
    conn: psycopg.Connection[Row],
    tenant_id: uuid.UUID,
    subject: Row,
    dob: str | None,
    requested_by: uuid.UUID | None,
    batch_id: uuid.UUID | None = None,
    identifiers: list[dict[str, str]] | None = None,
    embedder: Embedder | None = None,
) -> dict[str, Any]:
    config, version = load_config(conn, tenant_id)
    result = evaluate(conn, subject, dob, config, identifiers, embedder)
    breakdown = result["breakdown"]
    matches = result["watch"] + [
        {
            "match_type": "adverse_media",
            "entry_id": i.get("entry_id"),
            "mention_id": i["mention_id"],
            "name": i["surface"],
            "article": i["article"],
            "evidence": i["evidence"],
            "category": i["category"],
            "severity": i["severity"],
            "confidence": i["link_confidence"],
            "features": {"via": i["via"], "severity": i["severity"], "category": i["category"]},
        }
        for i in result["adverse"]
    ]
    label = f"{CONFIG_VERSION}.{version}"
    row = conn.execute(
        "INSERT INTO screenings (tenant_id, subject_id, requested_by, risk_score, risk_level, "
        "config_version, batch_id, score_breakdown) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING id, created_at",
        (
            tenant_id,
            subject["id"],
            requested_by,
            breakdown["score"],
            breakdown["level"],
            label,
            batch_id,
            Jsonb(
                {**breakdown, "notes": result["notes"]}, dumps=lambda o: json.dumps(o, default=str)
            ),
        ),
    ).fetchone()
    assert row is not None
    for match in matches:
        conn.execute(
            "INSERT INTO screening_matches (screening_id, match_type, entry_id, mention_id, "
            "confidence, features) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                row["id"],
                match["match_type"],
                match["entry_id"],
                match.get("mention_id"),
                match["confidence"],
                Jsonb(
                    {**match["features"], "name": match["name"]},
                    dumps=lambda o: json.dumps(o, default=str),
                ),
            ),
        )
    return {
        "id": row["id"],
        "subject": {k: subject[k] for k in ("id", "name", "kind", "country")},
        "risk_score": breakdown["score"],
        "risk_level": breakdown["level"],
        "score_breakdown": breakdown,
        "config_version": label,
        "matches": matches,
        "notes": result["notes"],
        "created_at": row["created_at"],
    }


def meter_usage(
    conn: psycopg.Connection[Row], tenant_id: uuid.UUID, screenings: int, batch: int
) -> None:
    conn.execute(
        "INSERT INTO usage_daily (tenant_id, day, screenings, batch_rows) "
        "VALUES (%s, current_date, %s, %s) ON CONFLICT (tenant_id, day) DO UPDATE SET "
        "screenings = usage_daily.screenings + EXCLUDED.screenings, "
        "batch_rows = usage_daily.batch_rows + EXCLUDED.batch_rows",
        (tenant_id, screenings, batch),
    )
