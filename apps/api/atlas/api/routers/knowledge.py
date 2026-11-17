"""Read-only knowledge base: entities, graph, watchlists, articles, and ingestion status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from atlas.api import graph_service
from atlas.api.deps import Authorized, Ctx
from atlas.api.errors import PROBLEM_RESPONSES, bad_request, not_found
from atlas.api.pagination import clamp_limit, decode_cursor, encode_cursor
from atlas.api.rbac import PLATFORM, VIEW
from atlas.names import normalize_name

entities = APIRouter(prefix="/entities", tags=["entities"], responses=PROBLEM_RESPONSES)
graph = APIRouter(prefix="/graph", tags=["graph"], responses=PROBLEM_RESPONSES)
watchlists = APIRouter(prefix="/watchlists", tags=["watchlists"], responses=PROBLEM_RESPONSES)
articles = APIRouter(prefix="/articles", tags=["articles"], responses=PROBLEM_RESPONSES)
ingestion = APIRouter(prefix="/ingestion", tags=["ingestion"], responses=PROBLEM_RESPONSES)


@entities.get("/search", summary="Search knowledge entities by name")
def search_entities(
    q: str = Query(min_length=2, max_length=200),
    kind: str | None = Query(default=None, pattern="^(person|organization)$"),
    limit: int = 20,
    ctx: Ctx = Authorized(VIEW),
) -> dict[str, Any]:
    limit = min(clamp_limit(limit), 50)
    norm = normalize_name(q, kind or "person")
    ctx.conn.execute("SELECT set_config('pg_trgm.similarity_threshold', '0.4', true)")
    rows = ctx.conn.execute(
        "SELECT id, qid, kind, name, description, countries, sim FROM ("
        " SELECT DISTINCT ON (id) id, qid, kind, name, description, countries, sim FROM ("
        "  SELECT e.id, e.qid, e.kind, e.name, e.description, e.countries, "
        "         similarity(e.name_norm, %(n)s) AS sim FROM entities e "
        "  WHERE e.name_norm %% %(n)s AND (%(k)s::text IS NULL OR e.kind = %(k)s)"
        "  UNION ALL"
        "  SELECT e.id, e.qid, e.kind, e.name, e.description, e.countries, "
        "         similarity(a.alias_norm, %(n)s) FROM entity_aliases a "
        "  JOIN entities e ON e.id = a.entity_id "
        "  WHERE a.alias_norm %% %(n)s AND (%(k)s::text IS NULL OR e.kind = %(k)s)"
        " ) u ORDER BY id, sim DESC) best ORDER BY sim DESC, COALESCE((SELECT sitelinks FROM "
        "entities x WHERE x.id = best.id), 0) DESC LIMIT %(lim)s",
        {"n": norm.stripped or norm.full, "k": kind, "lim": limit},
    ).fetchall()
    return {"items": rows}


@entities.get("/{entity_id}", summary="Entity detail")
def get_entity(entity_id: int, ctx: Ctx = Authorized(VIEW)) -> dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT id, qid, lei, kind, name, description, countries, birth_date, sitelinks, source "
        "FROM entities WHERE id = %s",
        (entity_id,),
    ).fetchone()
    if row is None:
        raise not_found("Entity")
    row["aliases"] = ctx.conn.execute(
        "SELECT alias, lang, script FROM entity_aliases WHERE entity_id = %s "
        "ORDER BY is_primary DESC, alias LIMIT 50",
        (entity_id,),
    ).fetchall()
    row["relationship_count"] = ctx.conn.execute(
        "SELECT count(*) AS n FROM entity_relationships WHERE src_id = %s OR dst_id = %s",
        (entity_id, entity_id),
    ).fetchone()["n"]  # type: ignore[index]
    row["watchlist_links"] = ctx.conn.execute(
        "SELECT w.id AS entry_id, w.name, s.code AS source, l.method, l.score, l.status "
        "FROM watchlist_entity_links l JOIN watchlist_entries w ON w.id = l.entry_id "
        "JOIN watchlist_sources s ON s.id = w.source_id WHERE l.entity_id = %s "
        "AND l.status IN ('accepted', 'review') ORDER BY l.score DESC",
        (entity_id,),
    ).fetchall()
    return row


@graph.get("/neighbors", summary="Entities connected to an entity")
def neighbors(
    entity_id: int, depth: int = Query(default=1, ge=1, le=3), ctx: Ctx = Authorized(VIEW)
) -> dict[str, Any]:
    if ctx.conn.execute("SELECT 1 FROM entities WHERE id = %s", (entity_id,)).fetchone() is None:
        raise not_found("Entity")
    return graph_service.neighbors(ctx.conn, entity_id, depth)


@graph.get("/path", summary="Shortest path between two entities")
def path(
    src: int, dst: int, max_depth: int = Query(default=4, ge=1, le=5), ctx: Ctx = Authorized(VIEW)
) -> dict[str, Any]:
    if src == dst:
        raise bad_request("src and dst must differ.")
    return graph_service.shortest_path(ctx.conn, src, dst, max_depth)


@watchlists.get("/sources", summary="Loaded watchlist sources")
def watchlist_sources(ctx: Ctx = Authorized(VIEW)) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT code, name, url, license, commercial_use, list_type, published_on, loaded_at, "
        "entry_count FROM watchlist_sources ORDER BY code"
    ).fetchall()
    return {"items": rows}


@watchlists.get("/entries", summary="Search watchlist entries")
def watchlist_entries(
    q: str | None = Query(default=None, max_length=200),
    source: str | None = Query(default=None, max_length=40),
    kind: str | None = Query(default=None, pattern="^(person|organization|vessel|aircraft)$"),
    cursor: str | None = None,
    limit: int = 25,
    ctx: Ctx = Authorized(VIEW),
) -> dict[str, Any]:
    limit = clamp_limit(limit)
    after = decode_cursor(cursor, 1)
    sql = (
        "SELECT e.id, e.name, e.kind, e.programs, e.countries, e.birth_years, e.listed_on, "
        "s.code AS source FROM watchlist_entries e JOIN watchlist_sources s ON s.id = e.source_id "
        "WHERE true"
    )
    params: list[Any] = []
    if q:
        norm = normalize_name(q, "person")
        sql += " AND (e.name_norm ILIKE %s OR e.name_key ILIKE %s)"
        params += [f"%{norm.full}%", f"%{norm.key}%"]
    if source:
        sql += " AND s.code = %s"
        params.append(source)
    if kind:
        sql += " AND e.kind = %s"
        params.append(kind)
    if after:
        sql += " AND e.id > %s"
        params.append(int(after[0]))
    sql += " ORDER BY e.id LIMIT %s"
    rows = ctx.conn.execute(sql, [*params, limit + 1]).fetchall()
    page = rows[:limit]
    return {
        "items": page,
        "next_cursor": encode_cursor([page[-1]["id"]]) if len(rows) > limit else None,
    }


@articles.get("", summary="Harvested articles")
def list_articles(
    language: str | None = Query(default=None, max_length=8),
    cursor: str | None = None,
    limit: int = 25,
    ctx: Ctx = Authorized(VIEW),
) -> dict[str, Any]:
    limit = clamp_limit(limit)
    after = decode_cursor(cursor, 1)
    sql = (
        "SELECT id, url, title, domain, language, source_country, published_at, topic "
        "FROM articles WHERE true"
    )
    params: list[Any] = []
    if language:
        sql += " AND language = %s"
        params.append(language)
    if after:
        sql += " AND id < %s"
        params.append(int(after[0]))
    sql += " ORDER BY id DESC LIMIT %s"
    rows = ctx.conn.execute(sql, [*params, limit + 1]).fetchall()
    page = rows[:limit]
    return {
        "items": page,
        "next_cursor": encode_cursor([page[-1]["id"]]) if len(rows) > limit else None,
    }


@articles.get("/{article_id}", summary="Article with linked mentions")
def get_article(article_id: int, ctx: Ctx = Authorized(VIEW)) -> dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT id, url, title, domain, language, source_country, published_at, topic, snippet "
        "FROM articles WHERE id = %s",
        (article_id,),
    ).fetchone()
    if row is None:
        raise not_found("Article")
    row["mentions"] = ctx.conn.execute(
        "SELECT m.id, m.surface, m.start_char, m.end_char, m.qid, m.entity_id, m.el_score, "
        "m.route, m.decision, e.name AS entity_name FROM mentions m "
        "LEFT JOIN entities e ON e.id = m.entity_id WHERE m.article_id = %s ORDER BY m.start_char",
        (article_id,),
    ).fetchall()
    return row


@ingestion.get("/status", summary="Ingestion and knowledge base counts")
def ingestion_status(ctx: Ctx = Authorized(PLATFORM)) -> dict[str, Any]:
    c = ctx.conn

    def one(sql: str) -> dict[str, Any] | None:
        return c.execute(sql).fetchone()

    articles_row = one(
        "SELECT count(*) AS total, max(harvested_at) AS last_harvest, "
        "count(*) FILTER (WHERE harvested_at > now() - interval '24 hours') AS last_24h "
        "FROM articles"
    )
    return {
        "articles": articles_row,
        "mentions": one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE decision <> 'nil') "
            "AS linked FROM mentions"
        ),
        "entities": one("SELECT count(*) AS total FROM entities"),
        "watchlist_entries": one("SELECT count(*) AS total FROM watchlist_entries"),
        "sources": c.execute(
            "SELECT code, entry_count, published_on, loaded_at FROM watchlist_sources ORDER BY code"
        ).fetchall(),
    }
