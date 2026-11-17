"""Estimate keyword prefilter recall on fetched article bodies (kept in memory only).

Positives are articles harvested by adverse-topic queries. This is weak evidence: the filter
vocabulary overlaps the harvest queries, and the queries match article bodies, so recall here
is optimistic. It is not a substitute for recall on the hand-labeled gold set.
"""

from __future__ import annotations

import collections
import json
import random
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "data" / "scripts"))

from atlas.nlp import guard  # noqa: E402
from common import USER_AGENT  # noqa: E402
from fetch_article_text import DomainLimiter, RobotsCache, fetch_article  # noqa: E402


def main(limit: int = 200) -> None:
    rows = []
    for path in sorted((ROOT / "data" / "raw" / "news" / "gdelt").glob("*.jsonl")):
        rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    rows = [r for r in rows if r["language"] == "en"]
    random.Random(3).shuffle(rows)
    by_domain: dict[str, int] = collections.Counter()
    picked = []
    for r in rows:  # at most 3 per domain keeps the fetch polite and the sample varied
        if by_domain[r["domain"]] < 3:
            by_domain[r["domain"]] += 1
            picked.append(r)
        if len(picked) >= limit * 2:
            break
    tested = passed = 0
    status: dict[str, int] = collections.Counter()
    limiter = DomainLimiter(2.0)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=False) as c:
        robots = RobotsCache(c)
        for r in picked:
            result, text = fetch_article(r["url"], c, robots, limiter)
            status[result.status] += 1
            if text:
                tested += 1
                passed += guard.lexical_adverse(text)
            if tested >= limit:
                break
    summary = {
        "fetch_status": dict(status),
        "bodies_tested": tested,
        "pass_rate": round(passed / tested, 3) if tested else None,
    }
    print(json.dumps(summary))
    out = ROOT / "docs" / "eval" / "prefilter.md"
    out.write_text(
        "# Keyword prefilter check\n\n"
        "Articles harvested by adverse-topic queries (English). Bodies were fetched, tested in "
        "memory, and discarded.\n\n```\n" + json.dumps(summary, indent=1) + "\n```\n\n"
        "Title-only recall on the harvested English titles was 11 percent, so the filter must "
        "run on "
        "article text, never on titles. The body result is optimistic because the filter "
        "vocabulary overlaps the harvest queries; recall on the gold set is still to be "
        "measured.\n"
    )


if __name__ == "__main__":
    main()
