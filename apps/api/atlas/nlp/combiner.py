"""Candidate-level logistic combiner for adjudicated mentions, with calibration.

Features per candidate: BELA linking score, BELA rank, cross-encoder probability (with a
presence flag), and the LLM choice with its confidence. The model predicts whether the candidate
is the correct entity; the best candidate wins, or NIL when its probability is below the
threshold. Parameters are plain lists so they can be stored in the versioned config.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

FEATURES = ["bela", "rank0", "blink", "blink_present", "llm_pick", "llm_conf_pick"]


@dataclass(frozen=True)
class CandidateFeatures:
    bela: float
    rank: int
    blink: float | None
    llm_pick: bool | None
    llm_conf: float

    def vector(self) -> list[float]:
        return [
            self.bela,
            1.0 if self.rank == 0 else 0.0,
            self.blink or 0.0,
            1.0 if self.blink is not None else 0.0,
            1.0 if self.llm_pick else 0.0,
            self.llm_conf if self.llm_pick else 0.0,
        ]


def predict_proba(weights: dict[str, Any], features: CandidateFeatures) -> float:
    z = float(weights["intercept"])
    for w, x in zip(weights["coef"], features.vector(), strict=True):
        z += float(w) * x
    return 1.0 / (1.0 + math.exp(-z))


def fit(rows: list[list[float]], labels: list[int]) -> dict[str, Any]:
    """Fit a regularized logistic regression and return its parameters."""
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression(C=1.0, max_iter=1000)
    model.fit(rows, labels)
    return {
        "features": FEATURES,
        "coef": [float(c) for c in model.coef_[0]],
        "intercept": float(model.intercept_[0]),
    }


def pick(
    weights: dict[str, Any], candidates: list[tuple[str, CandidateFeatures]], nil_threshold: float
) -> tuple[str | None, float]:
    """Return (qid or None for NIL, probability of the winner)."""
    if not candidates:
        return None, 0.0
    scored = [(predict_proba(weights, f), qid) for qid, f in candidates]
    probability, qid = max(scored)
    return (qid if probability >= nil_threshold else None), probability


def reliability_bins(
    probabilities: list[float], outcomes: list[int], bins: int = 10
) -> list[dict[str, float]]:
    """Reliability table: mean predicted probability against observed accuracy per bin."""
    table: list[dict[str, float]] = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probabilities) if lo <= p < hi or (b == bins - 1 and p == 1)]
        if idx:
            table.append(
                {
                    "bin_low": lo,
                    "bin_high": hi,
                    "count": len(idx),
                    "mean_predicted": sum(probabilities[i] for i in idx) / len(idx),
                    "observed": sum(outcomes[i] for i in idx) / len(idx),
                }
            )
    return table


def expected_calibration_error(
    probabilities: list[float], outcomes: list[int], bins: int = 10
) -> float:
    total = len(probabilities)
    if not total:
        return 0.0
    return sum(
        row["count"] / total * abs(row["mean_predicted"] - row["observed"])
        for row in reliability_bins(probabilities, outcomes, bins)
    )


def fit_isotonic(probabilities: list[float], outcomes: list[int]) -> dict[str, list[float]]:
    """Isotonic calibration; returns breakpoints for piecewise-linear interpolation."""
    from sklearn.isotonic import IsotonicRegression

    model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    model.fit(probabilities, outcomes)
    return {
        "x": [float(x) for x in model.X_thresholds_],
        "y": [float(y) for y in model.y_thresholds_],
    }


def apply_isotonic(curve: dict[str, list[float]], p: float) -> float:
    xs, ys = curve["x"], curve["y"]
    if not xs:
        return p
    if p <= xs[0]:
        return ys[0]
    if p >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if p <= xs[i]:
            span = xs[i] - xs[i - 1]
            return (
                ys[i - 1] if span == 0 else ys[i - 1] + (ys[i] - ys[i - 1]) * (p - xs[i - 1]) / span
            )
    return ys[-1]
