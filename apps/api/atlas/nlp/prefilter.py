"""Cheap pre-filter that skips articles with no adverse vocabulary (cost control).

The filter is a multilingual keyword test. It is deliberately permissive, because a missed
article cannot be recovered later. Its recall loss must be measured on labeled adverse
articles before it is enabled by default (see docs/eval/report.md).
"""

from __future__ import annotations

from atlas.nlp.guard import lexical_adverse


def passes(title: str | None, snippet_or_text: str | None) -> bool:
    return lexical_adverse(f"{title or ''} {snippet_or_text or ''}")
