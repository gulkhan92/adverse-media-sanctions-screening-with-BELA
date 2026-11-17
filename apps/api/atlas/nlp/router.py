"""Decision routing for linked mentions.

Routes (initial values, calibrated on held-out data and frozen in the versioned config):
  accept   top-1 score at or above ``accept_el`` and margin at or above ``accept_margin``
  nil      top-1 score below ``nil_el``
  blink    in between, English text: cross-encoder re-ranks the top candidates
  llm      in between, other languages: local LLM adjudicates among the top candidates
When an adjudicator is unavailable the mention falls back to the BELA decision and is flagged
``reduced_confidence``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from atlas.nlp.config import NlpConfig

Route = Literal["accept", "blink", "llm", "nil"]


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    top_score: float
    margin: float


def margin_of(scores: list[float]) -> float:
    if not scores:
        return 0.0
    ordered = sorted(scores, reverse=True)
    return ordered[0] - (ordered[1] if len(ordered) > 1 else 0.0)


def route_mention(scores: list[float], lang: str, cfg: NlpConfig) -> RouteDecision:
    """Choose the route from the candidate scores sorted or not (they are sorted here)."""
    if not scores:
        return RouteDecision("nil", 0.0, 0.0)
    top = max(scores)
    margin = margin_of(scores)
    if top < cfg.nil_el:
        return RouteDecision("nil", top, margin)
    if top >= cfg.accept_el and margin >= cfg.accept_margin:
        return RouteDecision("accept", top, margin)
    return RouteDecision("blink" if lang == "en" else "llm", top, margin)
