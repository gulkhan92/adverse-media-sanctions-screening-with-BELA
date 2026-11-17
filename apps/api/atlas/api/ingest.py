"""Article ingestion: store the article record, linked mentions, findings, and co-occurrence.

Only a short snippet of the article text is stored (data minimization). Entity co-occurrence is
computed at ingestion because it needs the sentence boundaries of the full text: two linked
entities that appear in the same sentence add that sentence to the weight of their edge.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import psycopg

from atlas.api.db import Row

SNIPPET_CHARS = 300


@dataclass
class FindingIn:
    category: str
    severity: int
    evidence_sentence: str
    prompt_version: str = "adverse-v1"
    model_version: str | None = None


@dataclass
class MentionIn:
    start: int
    end: int
    surface: str
    qid: str | None = None
    md_score: float | None = None
    el_score: float | None = None
    route: str = "accept"
    decision: str = "link"
    model_version: str | None = None
    findings: list[FindingIn] = field(default_factory=list)


@dataclass
class ArticleIn:
    url: str
    title: str
    text: str
    language: str = "en"
    domain: str | None = None
    source_country: str | None = None
    published_at: dt.datetime | None = None
    topic: str | None = None


def sentence_spans(text: str, language: str) -> list[tuple[int, int]]:
    """Character spans of sentences (pysbd, falling back to English rules)."""
    import pysbd

    try:
        segmenter = pysbd.Segmenter(language=language, clean=False, char_span=True)
    except ValueError:
        segmenter = pysbd.Segmenter(language="en", clean=False, char_span=True)
    return [(s.start, s.end) for s in segmenter.segment(text)]


def cooccurrence(
    mentions: list[tuple[int, int | None]], spans: list[tuple[int, int]]
) -> dict[tuple[int, int], int]:
    """Count sentences containing both entities. ``mentions`` holds (start char, entity id)."""
    by_sentence: dict[int, set[int]] = defaultdict(set)
    for start, entity_id in mentions:
        if entity_id is None:
            continue
        for index, (lo, hi) in enumerate(spans):
            if lo <= start < hi:
                by_sentence[index].add(entity_id)
                break
    counts: dict[tuple[int, int], int] = defaultdict(int)
    for entities in by_sentence.values():
        for a, b in combinations(sorted(entities), 2):
            counts[(a, b)] += 1
    return dict(counts)


def ingest_article(
    conn: psycopg.Connection[Row], article: ArticleIn, mentions: list[MentionIn]
) -> dict[str, Any]:
    """Insert or replace an article and its mentions. Returns ids and counts."""
    row = conn.execute(
        "INSERT INTO articles (url, title, domain, language, source_country, published_at, "
        "harvested_at, topic, snippet, fetch_status) VALUES (%s, %s, %s, %s, %s, %s, now(), %s, "
        "%s, 'ok') ON CONFLICT (url) DO UPDATE SET title = EXCLUDED.title, "
        "language = EXCLUDED.language, snippet = EXCLUDED.snippet, fetch_status = 'ok' "
        "RETURNING id",
        (
            article.url,
            article.title,
            article.domain,
            article.language,
            article.source_country,
            article.published_at,
            article.topic,
            article.text[:SNIPPET_CHARS],
        ),
    ).fetchone()
    assert row is not None
    article_id = row["id"]
    conn.execute("DELETE FROM entity_cooccurrence WHERE article_id = %s", (article_id,))
    conn.execute("DELETE FROM mentions WHERE article_id = %s", (article_id,))
    entity_of: dict[str, int] = {}
    qids = sorted({m.qid for m in mentions if m.qid})
    if qids:
        for r in conn.execute("SELECT id, qid FROM entities WHERE qid = ANY(%s)", (qids,)):
            entity_of[r["qid"]] = r["id"]
    resolved: list[tuple[int, int | None]] = []
    findings = 0
    for m in mentions:
        entity_id = entity_of.get(m.qid) if m.qid else None
        stored = conn.execute(
            "INSERT INTO mentions (article_id, start_char, end_char, surface, qid, entity_id, "
            "md_score, el_score, route, decision, model_version) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                article_id,
                m.start,
                m.end,
                m.surface,
                m.qid,
                entity_id,
                m.md_score,
                m.el_score,
                m.route,
                m.decision,
                m.model_version,
            ),
        ).fetchone()
        assert stored is not None
        resolved.append((m.start, entity_id))
        for f in m.findings:
            conn.execute(
                "INSERT INTO adverse_findings (mention_id, category, severity, evidence_sentence, "
                "prompt_version, model_version) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    stored["id"],
                    f.category,
                    f.severity,
                    f.evidence_sentence,
                    f.prompt_version,
                    f.model_version,
                ),
            )
            findings += 1
    pairs = cooccurrence(resolved, sentence_spans(article.text, article.language))
    for (a, b), sentences in pairs.items():
        conn.execute(
            "INSERT INTO entity_cooccurrence (entity_a, entity_b, article_id, sentences) "
            "VALUES (%s, %s, %s, %s)",
            (a, b, article_id, sentences),
        )
    return {
        "article_id": article_id,
        "mentions": len(mentions),
        "findings": findings,
        "cooccurrence_edges": len(pairs),
    }
