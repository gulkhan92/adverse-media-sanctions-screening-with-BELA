"""Risk score (PLAN.md phase 5, task 2).

    score = 100 * (w_list * list_weight * match_confidence
                   + w_adverse * adverse_severity * link_confidence
                   + w_recency * recency_decay
                   + w_source * source_reliability)

Every component is returned with its inputs so that the interface can show the breakdown.
With no match at all the score is 0 (recency and reliability describe a match, so they are
zero without one). Weights and thresholds are tenant-editable only inside the bounds below.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

SCORE_VERSION = "score-v1"
WEIGHT_BOUNDS = (0.05, 0.60)
THRESHOLD_BOUNDS = {"medium": (20.0, 50.0), "high": (50.0, 75.0), "critical": (70.0, 95.0)}

DEFAULT_SCORING: dict[str, Any] = {
    "weights": {"list": 0.40, "adverse": 0.35, "recency": 0.15, "source": 0.10},
    "list_weights": {"sanctions": 1.0, "pep": 0.6, "other": 0.5, "adverse_only": 0.4},
    "thresholds": {"medium": 40.0, "high": 60.0, "critical": 80.0},
    "recency_half_life_days": 365,
    "source_reliability": {"official_list": 1.0, "pep_list": 0.9, "other_list": 0.8, "news": 0.6},
}


def level_for(score: float, thresholds: dict[str, float]) -> str:
    if score >= thresholds["critical"]:
        return "critical"
    if score >= thresholds["high"]:
        return "high"
    if score >= thresholds["medium"]:
        return "medium"
    return "low"


def recency_decay(published: dt.datetime | None, now: dt.datetime, half_life_days: float) -> float:
    """Exponential decay: 1.0 today, 0.5 after one half-life. Unknown dates count as old."""
    if published is None:
        return 0.25
    if published.tzinfo is None:
        published = published.replace(tzinfo=dt.UTC)
    age_days = max(0.0, (now - published).total_seconds() / 86400)
    return float(math.exp(-math.log(2) * age_days / max(half_life_days, 1.0)))


def source_reliability_for(list_type: str | None, table: dict[str, float]) -> float:
    return {
        "sanctions": table["official_list"],
        "pep": table["pep_list"],
        None: table["news"],
    }.get(list_type, table["other_list"])


def compute_score(
    config: dict[str, Any],
    list_match: dict[str, Any] | None,
    adverse: list[dict[str, Any]],
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Return the score, level, and the breakdown of each component.

    ``list_match`` needs ``confidence`` and ``list_type``. Each ``adverse`` item needs
    ``severity`` (1 to 4), ``link_confidence``, ``published_at``, and ``confidence`` (how well
    the subject name matches the mention).
    """
    now = now or dt.datetime.now(dt.UTC)
    w = config["weights"]
    lw = config["list_weights"]
    half = float(config["recency_half_life_days"])
    best = max(adverse, key=lambda a: a["severity"] * a["link_confidence"], default=None)

    # First term: a list match, or the name match of the best adverse mention when the subject
    # is not on any list (list weight "adverse_only").
    if list_match is not None:
        list_weight = lw.get(list_match["list_type"], lw["other"])
        match_conf = float(list_match["confidence"])
    elif best is not None:
        list_weight = lw["adverse_only"]
        match_conf = float(best.get("confidence", best["link_confidence"]))
    else:
        list_weight, match_conf = 0.0, 0.0
    sev = (best["severity"] / 4.0) if best else 0.0
    link = float(best["link_confidence"]) if best else 0.0

    recency_inputs: list[float] = []
    reliability_inputs: list[float] = []
    if list_match is not None:
        recency_inputs.append(1.0)  # an active list entry is current
        reliability_inputs.append(
            source_reliability_for(list_match["list_type"], config["source_reliability"])
        )
    if best is not None:
        recency_inputs.append(recency_decay(best.get("published_at"), now, half))
        reliability_inputs.append(config["source_reliability"]["news"])
    recency = max(recency_inputs, default=0.0)
    reliability = max(reliability_inputs, default=0.0)

    parts = {
        "list": w["list"] * list_weight * match_conf,
        "adverse": w["adverse"] * sev * link,
        "recency": w["recency"] * recency,
        "source": w["source"] * reliability,
    }
    score = round(100 * sum(parts.values()), 2)
    level = level_for(score, config["thresholds"])
    return {
        "version": SCORE_VERSION,
        "score": score,
        "level": level,
        "components": {
            "list": {
                "weight": w["list"],
                "list_weight": list_weight,
                "match_confidence": round(match_conf, 4),
                "points": round(100 * parts["list"], 2),
            },
            "adverse": {
                "weight": w["adverse"],
                "severity": sev,
                "link_confidence": round(link, 4),
                "points": round(100 * parts["adverse"], 2),
            },
            "recency": {
                "weight": w["recency"],
                "decay": round(recency, 4),
                "half_life_days": half,
                "points": round(100 * parts["recency"], 2),
            },
            "source": {
                "weight": w["source"],
                "reliability": round(reliability, 4),
                "points": round(100 * parts["source"], 2),
            },
        },
        "thresholds": config["thresholds"],
    }
