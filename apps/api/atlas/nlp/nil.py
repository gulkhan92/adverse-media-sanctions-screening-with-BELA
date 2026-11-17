"""Matching of unlinked (NIL) mentions to subjects and watchlist aliases.

Private individuals rarely exist in Wikidata, so a mention that the linker rejects is compared
to known names by embedding similarity, fuzzy score, and context attributes. A match is
emitted only above a calibrated threshold and is labeled "name-based, unlinked".
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from atlas.names import normalize_name

LABEL = "name-based, unlinked"


@dataclass(frozen=True)
class NameCandidate:
    owner_id: int
    name: str
    countries: tuple[str, ...] = ()
    kind: str = "person"


@dataclass(frozen=True)
class NilMatch:
    owner_id: int
    score: float
    embedding_similarity: float
    fuzzy: float
    country_agreement: float
    label: str = LABEL


def fuzzy_score(a: str, b: str, kind: str = "person") -> float:
    """Token-set ratio on normalized names, scaled to 0..1."""
    na, nb = normalize_name(a, kind), normalize_name(b, kind)
    return fuzz.token_set_ratio(na.key, nb.key) / 100.0


def country_agreement(context_countries: set[str], candidate: NameCandidate) -> float:
    """1 when countries overlap, 0.5 when either side is unknown, 0 on conflict."""
    if not context_countries or not candidate.countries:
        return 0.5
    return 1.0 if context_countries & set(candidate.countries) else 0.0


def combine(embedding_similarity: float, fuzzy: float, country: float) -> float:
    """Weighted blend. Weights are fixed here and the threshold is calibrated on held-out data."""
    return 0.45 * embedding_similarity + 0.40 * fuzzy + 0.15 * country


def match_nil_mention(
    surface: str,
    candidates: list[tuple[NameCandidate, float]],
    context_countries: set[str],
    threshold: float,
) -> NilMatch | None:
    """Best candidate at or above ``threshold``. ``candidates`` carry embedding similarity."""
    best: NilMatch | None = None
    for candidate, similarity in candidates:
        fz = fuzzy_score(surface, candidate.name, candidate.kind)
        ca = country_agreement(context_countries, candidate)
        score = combine(similarity, fz, ca)
        if score >= threshold and (best is None or score > best.score):
            best = NilMatch(candidate.owner_id, score, similarity, fz, ca)
    return best
