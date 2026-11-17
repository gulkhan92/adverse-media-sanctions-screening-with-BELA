"""Metrics for entity linking evaluation (gold-span protocol).

Each evaluation record has a gold QID and ``in_kb``: whether the gold entity exists in the
linker's index. A prediction is a QID or None (NIL). Correct means: equal to the gold QID for
in-index mentions, and NIL for mentions whose gold entity is not in the index.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

Record = dict[str, Any]
Prediction = str | None


def counts(records: Sequence[Record], preds: Sequence[Prediction]) -> dict[str, int]:
    tp = fp = fn = nil_ok = nil_total = correct = 0
    for r, p in zip(records, preds, strict=True):
        if r["in_kb"]:
            if p == r["gold"]:
                tp += 1
                correct += 1
            else:
                fn += 1
                if p is not None:
                    fp += 1
        else:
            nil_total += 1
            if p is None:
                nil_ok += 1
                correct += 1
            else:
                fp += 1
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "nil_ok": nil_ok,
        "nil_total": nil_total,
        "correct": correct,
        "n": len(records),
    }


def prf(c: dict[str, int]) -> dict[str, float]:
    precision = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0.0
    recall = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": c["correct"] / c["n"] if c["n"] else 0.0,
        "nil_accuracy": c["nil_ok"] / c["nil_total"] if c["nil_total"] else float("nan"),
    }


def evaluate(records: Sequence[Record], preds: Sequence[Prediction]) -> dict[str, float]:
    return {**prf(counts(records, preds)), "n": float(len(records))}


def by_language(
    records: Sequence[Record], preds: Sequence[Prediction]
) -> dict[str, dict[str, float]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        groups[r["lang"]].append(i)
    return {
        lang: evaluate([records[i] for i in idx], [preds[i] for i in idx])
        for lang, idx in sorted(groups.items())
    }


def recall_at(
    records: Sequence[Record], ranked: Sequence[Sequence[str]], ks: tuple[int, ...] = (1, 5, 10)
) -> dict[str, float]:
    """Share of in-index mentions whose gold QID is among the first k ranked candidates."""
    rows = [(r, c) for r, c in zip(records, ranked, strict=True) if r["in_kb"]]
    if not rows:
        return {f"recall@{k}": float("nan") for k in ks}
    return {f"recall@{k}": sum(1 for r, c in rows if r["gold"] in c[:k]) / len(rows) for k in ks}


def paired_bootstrap_f1_gain(
    records: Sequence[Record],
    preds_a: Sequence[Prediction],
    preds_b: Sequence[Prediction],
    samples: int = 1000,
    seed: int = 7,
) -> tuple[float, float, float]:
    """Mean gain in F1 of B over A with a 95 percent bootstrap interval (paired by mention)."""
    rng = random.Random(seed)
    n = len(records)
    if n == 0:
        return 0.0, 0.0, 0.0
    gains = []
    for _ in range(samples):
        idx = [rng.randrange(n) for _ in range(n)]
        sub = [records[i] for i in idx]
        fa = evaluate(sub, [preds_a[i] for i in idx])["f1"]
        fb = evaluate(sub, [preds_b[i] for i in idx])["f1"]
        gains.append(fb - fa)
    gains.sort()
    return sum(gains) / samples, gains[int(0.025 * samples)], gains[int(0.975 * samples)]


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def grid_search(
    grid: dict[str, Sequence[float]],
    score: Callable[[dict[str, float]], tuple[float, float]],
) -> tuple[dict[str, float], tuple[float, float]]:
    """Exhaustive search. ``score`` returns (objective to maximize, tie-break to minimize)."""
    best: dict[str, float] = {}
    best_key: tuple[float, float] | None = None
    names = list(grid)

    def walk(i: int, current: dict[str, float]) -> None:
        nonlocal best, best_key
        if i == len(names):
            objective, penalty = score(current)
            key = (objective, -penalty)
            if best_key is None or key > best_key:
                best, best_key = dict(current), key
            return
        for value in grid[names[i]]:
            walk(i + 1, {**current, names[i]: value})

    walk(0, {})
    assert best_key is not None
    return best, (best_key[0], -best_key[1])
