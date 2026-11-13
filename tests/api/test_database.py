from __future__ import annotations

from typing import Any

import numpy as np
import psycopg
from atlas import quality, resolution
from atlas.db import copy_rows
from atlas.embeddings import (
    KB_PREDICATE,
    WATCHLIST_PREDICATE,
    HashingEmbedder,
    embed_missing,
    search,
)
from atlas.names import normalize_name
from pgvector.psycopg import register_vector


def add_source(conn: psycopg.Connection[Any]) -> int:
    row = conn.execute(
        "INSERT INTO watchlist_sources (code, name, license, commercial_use, list_type) "
        "VALUES ('t', 'test', 'test', true, 'sanctions') RETURNING id"
    ).fetchone()
    assert row
    return int(row[0])


def add_entry(
    conn: psycopg.Connection[Any], source: int, key: str, name: str, kind: str, **extra: Any
) -> int:
    n = normalize_name(name, kind)
    row = conn.execute(
        "INSERT INTO watchlist_entries (source_id, source_key, kind, name, name_norm, name_key,"
        " countries, birth_years, identifiers) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            source,
            key,
            kind,
            name,
            n.stripped,
            n.key,
            extra.get("countries", []),
            extra.get("birth_years", []),
            extra.get("identifiers", "[]"),
        ),
    ).fetchone()
    assert row
    return int(row[0])


def add_entity(
    conn: psycopg.Connection[Any], name: str, kind: str, qid: str | None = None, **extra: Any
) -> int:
    n = normalize_name(name, kind)
    row = conn.execute(
        "INSERT INTO entities (kind, qid, lei, name, name_norm, name_key, countries, birth_date,"
        " source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'test') RETURNING id",
        (
            kind,
            qid,
            extra.get("lei"),
            name,
            n.stripped,
            n.key,
            extra.get("countries", []),
            extra.get("birth_date"),
        ),
    ).fetchone()
    assert row
    return int(row[0])


def links(conn: psycopg.Connection[Any]) -> dict[tuple[int, int], tuple[str, float, str]]:
    rows = conn.execute(
        "SELECT entry_id, entity_id, method, score, status FROM watchlist_entity_links"
    ).fetchall()
    return {(r[0], r[1]): (r[2], float(r[3]), r[4]) for r in rows}


def test_extensions_and_tables_exist(conn: psycopg.Connection[Any]) -> None:
    ext = {r[0] for r in conn.execute("SELECT extname FROM pg_extension").fetchall()}
    assert {"vector", "pg_trgm", "pgcrypto"} <= ext
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    }
    expected = {
        "tenants",
        "users",
        "user_mfa",
        "refresh_tokens",
        "api_keys",
        "audit_log",
        "entities",
        "entity_aliases",
        "entity_embeddings",
        "entity_relationships",
        "watchlist_sources",
        "watchlist_entries",
        "watchlist_aliases",
        "articles",
        "article_embeddings",
        "mentions",
        "mention_candidates",
        "adverse_findings",
        "subjects",
        "screenings",
        "screening_matches",
        "alerts",
        "alert_events",
        "cases",
        "case_alerts",
        "case_notes",
        "plans",
        "subscriptions",
        "invoices",
        "invoice_lines",
        "usage_daily",
        "alert_stats_daily",
        "marketing_spend",
        "business_events",
    }
    assert expected <= tables
    views = {r[0] for r in conn.execute("SELECT matviewname FROM pg_matviews").fetchall()}
    assert {"mrr_monthly", "cohort_retention"} <= views


def test_unique_watchlist_key_enforced(conn: psycopg.Connection[Any]) -> None:
    source = add_source(conn)
    add_entry(conn, source, "1", "Alpha Beta", "person")
    try:
        add_entry(conn, source, "1", "Other Name", "person")
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        return
    raise AssertionError("duplicate watchlist key was accepted")


def test_resolution_by_identifier_and_name(conn: psycopg.Connection[Any]) -> None:
    source = add_source(conn)
    lei = "529900T8BM49AURSDO55"
    org = add_entry(
        conn,
        source,
        "10",
        "Nordic Metals Trading Ltd",
        "organization",
        identifiers=f'[{{"type": "Legal Entity Identifier", "value": "{lei}"}}]',
    )
    person_ok = add_entry(
        conn, source, "11", "Ivan Petrov", "person", countries=["RU"], birth_years=[1970]
    )
    person_conflict = add_entry(conn, source, "12", "Maria Garcia", "person", countries=["ES"])
    ambiguous = add_entry(conn, source, "13", "John Walker", "person")

    e_org = add_entity(conn, "Nordic Metals Trading", "organization", "Q1", lei=lei)
    e_ok = add_entity(
        conn, "Ivan Petrov", "person", "Q2", countries=["RU"], birth_date="1970-05-01"
    )
    e_conflict = add_entity(conn, "Maria Garcia", "person", "Q3", countries=["MX"])
    e_a1 = add_entity(conn, "John Walker", "person", "Q4")
    e_a2 = add_entity(conn, "John Walker", "person", "Q5")

    result = resolution.resolve(conn, use_embeddings=False)
    found = links(conn)
    assert found[(org, e_org)][0] == "identifier" and found[(org, e_org)][2] == "accepted"
    assert found[(person_ok, e_ok)][2] == "accepted" and found[(person_ok, e_ok)][1] >= 0.95
    assert found[(person_conflict, e_conflict)][2] == "rejected"  # country conflict
    assert found[(ambiguous, e_a1)][2] == "review" and found[(ambiguous, e_a2)][2] == "review"
    assert result["coverage"]["accepted_entries"] == 2


def test_hash_embeddings_and_partial_index_search(conn: psycopg.Connection[Any]) -> None:
    register_vector(conn)
    source = add_source(conn)
    entry = add_entry(conn, source, "20", "Mohammad Ali Hassan Trading", "organization")
    other = add_entry(conn, source, "21", "Completely Different Name", "organization")
    ent = add_entity(conn, "Mohammad Ali Hassan Trading", "organization", "Q10")
    embedder = HashingEmbedder()
    embed_missing(conn, embedder, "watchlist_entry", log=False)
    embed_missing(conn, embedder, "entity", log=False)
    query = embedder.encode(["Mohamad Ali Hassan Tradng"])[0]
    wl = search(
        conn, np.asarray(query), WATCHLIST_PREDICATE, k=2, model_version=embedder.model_version
    )
    assert wl[0][2] == entry and wl[0][2] != other
    kb = search(conn, np.asarray(query), KB_PREDICATE, k=2, model_version=embedder.model_version)
    assert kb[0][2] == ent
    assert quality.run_checks  # quality module imports


def test_quality_checks_pass_on_clean_data(conn: psycopg.Connection[Any]) -> None:
    source = add_source(conn)
    add_entry(conn, source, "30", "Clean Name", "person", countries=["US"], birth_years=[1980])
    add_entity(conn, "Clean Entity", "organization", "Q30", countries=["GB"])
    failures = [c for c in quality.run_checks(conn) if not c.passed]
    assert not failures, failures


def test_quality_detects_bad_country_and_duplicate_qid(conn: psycopg.Connection[Any]) -> None:
    source = add_source(conn)
    add_entry(conn, source, "40", "Bad Country", "person", countries=["ZZ"])
    results = {c.name: c for c in quality.run_checks(conn)}
    assert not results["valid ISO 3166-1 country codes: watchlist_entries"].passed
    copy_rows(conn, "articles", ["url", "snippet"], [("https://a.test/x", "ok")])
    long_snippet = "x" * 301
    try:
        conn.execute(
            "INSERT INTO articles (url, snippet) VALUES ('https://a.test/y', %s)", (long_snippet,)
        )
    except psycopg.errors.CheckViolation:
        conn.rollback()
    else:
        raise AssertionError("long snippet accepted")
