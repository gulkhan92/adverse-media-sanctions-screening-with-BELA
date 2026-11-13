"""Load harvested GDELT article metadata and fetched snippets into ``articles``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atlas.db import connect


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load(raw_dir: Path, snippets_path: Path) -> dict[str, int]:
    snippets = {r["url"]: r for r in read_jsonl(snippets_path)}
    rows: dict[str, tuple[Any, ...]] = {}
    for path in sorted(raw_dir.glob("*.jsonl")):
        for r in read_jsonl(path):
            url = r.get("url")
            if not url or url in rows:
                continue
            snip = snippets.get(url, {})
            rows[url] = (
                url,
                snip.get("title") or r.get("title"),
                r.get("domain"),
                snip.get("language") or r.get("language"),
                r.get("source_country"),
                r.get("seen_at"),
                r.get("harvested_at"),
                r.get("topic"),
                snip.get("snippet"),
                snip.get("status"),
            )
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO articles (url, title, domain, language, source_country, published_at,
                   harvested_at, topic, snippet, fetch_status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (url) DO UPDATE SET
                   title = EXCLUDED.title, language = EXCLUDED.language,
                   snippet = COALESCE(EXCLUDED.snippet, articles.snippet),
                   fetch_status = COALESCE(EXCLUDED.fetch_status, articles.fetch_status)""",
            list(rows.values()),
        )
        conn.commit()
    return {"articles": len(rows), "with_snippet": sum(1 for r in rows.values() if r[8])}


if __name__ == "__main__":
    from atlas.config import PROCESSED_DIR, RAW_DIR

    print(load(RAW_DIR / "news" / "gdelt", PROCESSED_DIR / "articles" / "article_snippets.jsonl"))
