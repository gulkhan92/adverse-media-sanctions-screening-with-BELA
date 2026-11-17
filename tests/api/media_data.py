"""Fixtures for adverse media: a linked entity, an article, a mention, and a finding."""

from __future__ import annotations

import datetime as dt
from typing import Any


def add_media(seed: dict[str, Any], ids: dict[str, Any], severity: int = 4) -> dict[str, Any]:
    """Link the first entity to the listed person and attach one adverse mention to it."""
    db = seed["admin"]
    entry = db.execute(
        "SELECT id FROM watchlist_entries WHERE kind = 'person' ORDER BY id LIMIT 1"
    ).fetchone()[0]
    entity = ids["entities"][0]
    db.execute(
        "INSERT INTO watchlist_entity_links (entry_id, entity_id, method, score, status) "
        "VALUES (%s, %s, 'name_country', 0.95, 'accepted')",
        (entry, entity),
    )
    article = db.execute(
        "INSERT INTO articles (url, title, domain, language, published_at, snippet) "
        "VALUES ('https://news.example/a1', 'Investigation reported', 'news.example', 'en', %s, "
        "'snippet') RETURNING id",
        (dt.datetime.now(dt.UTC) - dt.timedelta(days=10),),
    ).fetchone()[0]
    mention = db.execute(
        "INSERT INTO mentions (article_id, start_char, end_char, surface, qid, entity_id, "
        "el_score, route, decision) VALUES (%s, 0, 14, 'Alpha Holdings', 'Q1', %s, 0.9, "
        "'accept', 'link') RETURNING id",
        (article, entity),
    ).fetchone()[0]
    db.execute(
        "INSERT INTO adverse_findings (mention_id, category, severity, evidence_sentence, "
        "prompt_version) VALUES (%s, 'fraud', %s, 'Alpha Holdings was accused of fraud.', 'v1')",
        (mention, severity),
    )
    return {"entry": entry, "entity": entity, "article": article, "mention": mention}
