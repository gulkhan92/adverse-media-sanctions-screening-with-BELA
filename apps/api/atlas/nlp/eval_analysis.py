"""Evaluation analysis: threshold tuning on dev, results on test, and report generation.

Protocol (Mewsli-X, fixed-seed sample): the dev split is used to fit the combiner, tune the
routing thresholds, and fit calibration. The test split is reported once. All systems link a
given gold span (disambiguation mode), so precision is defined over the gold mentions only.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atlas.config import REPO_ROOT
from atlas.names import fold
from atlas.nlp import combiner, metrics
from atlas.nlp.config import NlpConfig
from atlas.nlp.eval_stages import EVAL_DIR, load_info, read_jsonl
from atlas.nlp.router import route_mention

Rec = dict[str, Any]


def load_records() -> list[Rec]:
    sample = {r["id"]: r for r in read_jsonl(EVAL_DIR / "sample.jsonl")}
    parts = {
        name: {r["id"]: r for r in read_jsonl(EVAL_DIR / f"{name}.jsonl")}
        for name in ("bela", "fuzzy", "blink", "llm")
    }
    store = load_info()
    # Surface collision: how many distinct entities in the universe share the folded surface.
    surface_owners: dict[str, set[str]] = defaultdict(set)
    for qid, info in store.items.items():
        for text in [*info.labels.values(), *[a for v in info.aliases.values() for a in v]]:
            surface_owners[fold(text)].add(qid)
    out: list[Rec] = []
    for mid, s in sample.items():
        b = parts["bela"].get(mid)
        if not b or not b["aligned"]:
            continue
        surface = s["text"][s["start"] : s["end"]]
        rec: Rec = {
            **s,
            "surface": surface,
            "in_kb": b["in_kb"],
            "bela": sorted(b["cands"], key=lambda c: c["score"], reverse=True),
            "detected": b["detected"],
            "fuzzy": parts["fuzzy"].get(mid, {}).get("cands", []),
            "blink": parts["blink"].get(mid, {}).get("bela", {}),
            "blink_fuzzy": parts["blink"].get(mid, {}).get("fuzzy", {}),
            "llm": parts["llm"].get(mid),
            "collision": len(surface_owners.get(fold(surface), set())) >= 2,
        }
        out.append(rec)
    return out


# --- predictions -----------------------------------------------------------------------


def pred_fuzzy(r: Rec) -> str | None:
    return r["fuzzy"][0]["qid"] if r["fuzzy"] else None


def pred_bela(r: Rec, nil_el: float = 0.40) -> str | None:
    return r["bela"][0]["qid"] if r["bela"] and r["bela"][0]["score"] >= nil_el else None


def pred_blink_alone(r: Rec) -> str | None:
    probs = r["blink_fuzzy"]
    return max(probs, key=lambda q: probs[q]) if probs else None


def candidate_rows(r: Rec, cfg: NlpConfig) -> list[tuple[str, combiner.CandidateFeatures]]:
    """Features for the adjudicated candidates of one mention (BLINK for en, LLM otherwise)."""
    if r["lang"] == "en":
        probs = r["blink"]
        return [
            (c["qid"], combiner.CandidateFeatures(c["score"], i, probs.get(c["qid"]), None, 0.0))
            for i, c in enumerate(r["bela"][: cfg.blink_candidates])
            if c["qid"] in probs
        ]
    answer = r["llm"]
    if not answer or not answer["ok"]:
        return []
    return [
        (
            c["qid"],
            combiner.CandidateFeatures(
                c["score"], i, None, c["qid"] == answer["choice"], answer["confidence"]
            ),
        )
        for i, c in enumerate(r["bela"][: cfg.llm_candidates])
    ]


def hybrid(r: Rec, cfg: NlpConfig) -> tuple[str | None, str, float]:
    """Return (prediction, route label, confidence). Mirrors MentionResolver."""
    scores = [c["score"] for c in r["bela"]]
    route = route_mention(scores, r["lang"], cfg)
    if route.route == "nil":
        return None, "nil", route.top_score
    if route.route == "accept":
        return r["bela"][0]["qid"], "accept", route.top_score
    rows = candidate_rows(r, cfg)
    if not rows:  # adjudicator output missing: BELA decision, reduced confidence
        return pred_bela(r, cfg.nil_el), "fallback", route.top_score
    if cfg.combiner:
        qid, prob = combiner.pick(cfg.combiner, rows, cfg.adjudication_nil_threshold)
    elif r["lang"] == "en":
        qid, prob = max(((q, f.blink or 0.0) for q, f in rows), key=lambda t: t[1])
    else:
        picked = [q for q, f in rows if f.llm_pick]
        qid, prob = (picked[0] if picked else None), rows[0][1].llm_conf
    return qid, route.route, prob


# --- fitting ---------------------------------------------------------------------------


def fit_combiner(dev: list[Rec], cfg: NlpConfig) -> dict[str, Any]:
    rows: list[list[float]] = []
    labels: list[int] = []
    for r in dev:
        for qid, feat in candidate_rows(r, cfg):
            rows.append(feat.vector())
            labels.append(int(r["in_kb"] and qid == r["gold"]))
    return combiner.fit(rows, labels)


def tune(dev: list[Rec], base: NlpConfig) -> tuple[NlpConfig, dict[str, Any]]:
    grid = {
        "accept_el": [0.5, 0.6, 0.7, 0.8, 0.85],
        "accept_margin": [0.05, 0.10, 0.15, 0.20, 0.30],
        "nil_el": [0.25, 0.30, 0.40, 0.50, 0.60],
        "adjudication_nil_threshold": [0.3, 0.4, 0.5, 0.6],
    }

    def score(p: dict[str, float]) -> tuple[float, float]:
        cfg = replace(base, **p)
        preds, routed = [], 0
        for r in dev:
            qid, route, _ = hybrid(r, cfg)
            preds.append(qid)
            routed += route in ("blink", "llm", "fallback")
        return metrics.evaluate(dev, preds)["f1"], routed / len(dev)

    best, (f1, adjud) = metrics.grid_search(grid, score)
    return replace(base, **best), {"dev_f1": f1, "dev_adjudication_fraction": adjud, "grid": grid}


def calibrate(dev: list[Rec], cfg: NlpConfig) -> dict[str, Any]:
    """Isotonic calibration per route on dev: confidence versus observed correctness."""
    by_route: dict[str, tuple[list[float], list[int]]] = defaultdict(lambda: ([], []))
    for r in dev:
        qid, route, conf = hybrid(r, cfg)
        if route in ("nil", "fallback"):
            continue
        key = "accept" if route == "accept" else "adjudicated"
        correct = int(qid == r["gold"] if r["in_kb"] else qid is None)
        by_route[key][0].append(conf)
        by_route[key][1].append(correct)
    return {k: combiner.fit_isotonic(v[0], v[1]) for k, v in by_route.items() if len(v[0]) >= 20}


# --- reporting -------------------------------------------------------------------------


def system_table(records: list[Rec], cfg: NlpConfig) -> dict[str, list[str | None]]:
    preds: dict[str, list[str | None]] = {
        "rapidfuzz": [pred_fuzzy(r) for r in records],
        "bela": [pred_bela(r, cfg.nil_el) for r in records],
        "hybrid": [hybrid(r, cfg)[0] for r in records],
    }
    return preds


def fmt(x: float) -> str:
    return "n/a" if isinstance(x, float) and math.isnan(x) else f"{x:.3f}"


def analyze(timing: dict[str, Any] | None = None) -> dict[str, Any]:
    records = load_records()
    dev = [r for r in records if r["split"] == "dev"]
    test = [r for r in records if r["split"] == "test"]
    base = NlpConfig(version="nlp_config_v1")
    base.combiner = fit_combiner(dev, base)
    cfg, tuning = tune(dev, base)
    cfg.calibration = calibrate(dev, cfg)
    initial = NlpConfig(version="initial", combiner=base.combiner)

    results: dict[str, Any] = {"n_dev": len(dev), "n_test": len(test), "tuning": tuning}
    for name, c in (("initial", initial), ("tuned", cfg)):
        preds = system_table(test, c)
        results[name] = {k: metrics.evaluate(test, v) for k, v in preds.items()}
        results[f"{name}_by_language"] = {k: metrics.by_language(test, v) for k, v in preds.items()}
        routes = [hybrid(r, c)[1] for r in test]
        results[f"{name}_routes"] = {
            k: routes.count(k) / len(routes) for k in ("accept", "blink", "llm", "nil", "fallback")
        }
    preds = system_table(test, cfg)
    en = [i for i, r in enumerate(test) if r["lang"] == "en"]
    en_rec = [test[i] for i in en]
    results["blink_alone_en"] = metrics.evaluate(en_rec, [pred_blink_alone(r) for r in en_rec])
    results["bela_en"] = metrics.evaluate(en_rec, [preds["bela"][i] for i in en])
    results["hybrid_en"] = metrics.evaluate(en_rec, [preds["hybrid"][i] for i in en])
    results["rapidfuzz_en"] = metrics.evaluate(en_rec, [preds["rapidfuzz"][i] for i in en])

    ranked_bela = [[c["qid"] for c in r["bela"]] for r in test]
    ranked_fuzzy = [[c["qid"] for c in r["fuzzy"]] for r in test]
    results["retrieval"] = {
        "bela": metrics.recall_at(test, ranked_bela),
        "rapidfuzz": metrics.recall_at(test, ranked_fuzzy),
    }
    results["retrieval_by_language"] = {
        lang: {
            "bela": metrics.recall_at(
                [r for r in test if r["lang"] == lang],
                [[c["qid"] for c in r["bela"]] for r in test if r["lang"] == lang],
            ),
            "rapidfuzz": metrics.recall_at(
                [r for r in test if r["lang"] == lang],
                [[c["qid"] for c in r["fuzzy"]] for r in test if r["lang"] == lang],
            ),
        }
        for lang in sorted({r["lang"] for r in test})
    }
    # Mention detection (gold span found by the detector at threshold 0.2).
    exact = overlap = 0
    for r in test:
        spans = [(d["start"], d["end"]) for d in r["detected"]]
        exact += (r["start"], r["end"]) in spans
        overlap += any(s < r["end"] and r["start"] < e for s, e in spans)
    results["mention_detection"] = {
        "exact_recall": exact / len(test),
        "overlap_recall": overlap / len(test),
        "mentions_per_passage": sum(len(r["detected"]) for r in test) / len(test),
    }
    # Subsets.
    ambiguous = [
        i
        for i, r in enumerate(test)
        if route_mention([c["score"] for c in r["bela"]], r["lang"], cfg).route in ("blink", "llm")
    ]
    sub = [test[i] for i in ambiguous]
    gain = metrics.paired_bootstrap_f1_gain(
        sub, [preds["bela"][i] for i in ambiguous], [preds["hybrid"][i] for i in ambiguous]
    )
    results["ambiguous"] = {
        "n": len(sub),
        "bela_f1": metrics.evaluate(sub, [preds["bela"][i] for i in ambiguous])["f1"],
        "hybrid_f1": metrics.evaluate(sub, [preds["hybrid"][i] for i in ambiguous])["f1"],
        "gain_mean": gain[0],
        "gain_ci95": [gain[1], gain[2]],
    }
    coll = [i for i, r in enumerate(test) if r["collision"]]
    csub = [test[i] for i in coll]
    results["collision"] = {
        "n": len(csub),
        **{k: metrics.evaluate(csub, [v[i] for i in coll]) for k, v in preds.items()},
    }
    # Adjudication accuracy.
    llm_rows = [
        (r, r["llm"])
        for r in test
        if r["llm"]
        and r["llm"]["ok"]
        and r["in_kb"]
        and r["gold"] in [c["qid"] for c in r["bela"][:5]]
    ]
    results["llm_adjudication"] = {
        "n": len(llm_rows),
        "accuracy_when_gold_in_top5": sum(1 for r, a in llm_rows if a["choice"] == r["gold"])
        / len(llm_rows)
        if llm_rows
        else float("nan"),
        "bela_top1_same_rows": sum(1 for r, _ in llm_rows if r["bela"][0]["qid"] == r["gold"])
        / len(llm_rows)
        if llm_rows
        else float("nan"),
        "failures": sum(1 for r in test if r["llm"] and not r["llm"]["ok"]),
    }
    blink_rows = [
        r
        for r in test
        if r["lang"] == "en" and r["in_kb"] and r["blink"] and r["gold"] in r["blink"]
    ]
    results["blink_adjudication"] = {
        "n": len(blink_rows),
        "accuracy_when_gold_in_top10": sum(
            1 for r in blink_rows if max(r["blink"], key=lambda q: r["blink"][q]) == r["gold"]
        )
        / len(blink_rows)
        if blink_rows
        else float("nan"),
        "bela_top1_same_rows": sum(1 for r in blink_rows if r["bela"][0]["qid"] == r["gold"])
        / len(blink_rows)
        if blink_rows
        else float("nan"),
    }
    # Calibration of the reported confidence on test, before and after isotonic.
    cal: dict[str, Any] = {}
    for route_key in ("accept", "adjudicated"):
        raw_p, cal_p, outcome = [], [], []
        for r in test:
            qid, route, conf = hybrid(r, cfg)
            key = "accept" if route == "accept" else "adjudicated"
            if route in ("nil", "fallback") or key != route_key:
                continue
            raw_p.append(conf)
            cal_p.append(
                combiner.apply_isotonic(cfg.calibration[key], conf)
                if key in cfg.calibration
                else conf
            )
            outcome.append(int(qid == r["gold"] if r["in_kb"] else qid is None))
        if raw_p:
            cal[route_key] = {
                "n": len(raw_p),
                "ece_raw": combiner.expected_calibration_error(raw_p, outcome),
                "ece_calibrated": combiner.expected_calibration_error(cal_p, outcome),
                "bins_raw": combiner.reliability_bins(raw_p, outcome),
                "bins_calibrated": combiner.reliability_bins(cal_p, outcome),
            }
    results["calibration"] = cal
    results["timing"] = timing or {}
    results["nil_share"] = sum(1 for r in test if not r["in_kb"]) / len(test)
    results["config"] = {
        k: getattr(cfg, k)
        for k in ("accept_el", "accept_margin", "nil_el", "adjudication_nil_threshold")
    }
    cfg.save()
    write_outputs(results, test, preds, cfg)
    return results


def write_outputs(
    results: dict[str, Any], test: list[Rec], preds: dict[str, list[str | None]], cfg: NlpConfig
) -> None:
    out = REPO_ROOT / "docs" / "eval"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "per_language.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["system", "lang", "n", "precision", "recall", "f1", "accuracy", "nil_accuracy"]
        )
        for system, langs in results["tuned_by_language"].items():
            for lang, m in langs.items():
                writer.writerow(
                    [
                        system,
                        lang,
                        int(m["n"]),
                        *[
                            round(m[k], 4)
                            for k in ("precision", "recall", "f1", "accuracy", "nil_accuracy")
                        ],
                    ]
                )
    with (out / "predictions_test.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "lang", "gold", "in_index", "rapidfuzz", "bela", "hybrid"])
        for i, r in enumerate(test):
            writer.writerow(
                [
                    r["id"],
                    r["lang"],
                    r["gold"],
                    r["in_kb"],
                    preds["rapidfuzz"][i],
                    preds["bela"][i],
                    preds["hybrid"][i],
                ]
            )
    public = REPO_ROOT / "public"
    public.mkdir(exist_ok=True)
    summary = {
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": "Mewsli-X WikiNews mentions, fixed-seed sample, gold-span protocol",
        "n_test": results["n_test"],
        "config": results["config"],
        "systems": results["tuned"],
        "retrieval": results["retrieval"],
        "routes": results["tuned_routes"],
        "ambiguous_subset": results["ambiguous"],
        "timing": results["timing"],
    }
    (public / "metrics.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    (out / "results.json").write_text(json.dumps(results, indent=2, default=float) + "\n")
    plot_reliability(results["calibration"], out / "reliability.png")
    (out / "report.md").write_text(render_report(results, cfg))


def plot_reliability(cal: dict[str, Any], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(
        1, max(len(cal), 1), figsize=(5 * max(len(cal), 1), 4.2), squeeze=False
    )
    for ax, (route, data) in zip(axes[0], cal.items(), strict=False):
        ax.plot([0, 1], [0, 1], color="#6C7A89", linestyle="--", label="perfect calibration")
        for key, color, label in (
            ("bins_raw", "#D55E5E", "raw"),
            ("bins_calibrated", "#2F74D8", "isotonic"),
        ):
            bins = data[key]
            ax.plot(
                [b["mean_predicted"] for b in bins],
                [b["observed"] for b in bins],
                marker="o",
                color=color,
                label=label,
            )
        ax.set_title(
            f"{route} route (n={data['n']}, ECE {data['ece_raw']:.3f} to {data['ece_calibrated']:.3f})",
            fontsize=10,
        )
        ax.set_xlabel("predicted confidence")
        ax.set_ylabel("observed accuracy")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def render_report(res: dict[str, Any], cfg: NlpConfig) -> str:
    def row(name: str, m: dict[str, float]) -> str:
        return f"| {name} | {int(m['n'])} | {fmt(m['precision'])} | {fmt(m['recall'])} | {fmt(m['f1'])} | {fmt(m['accuracy'])} | {fmt(m['nil_accuracy'])} |"

    head = "| System | Mentions | Precision | Recall | F1 | Accuracy | NIL accuracy |\n|---|---:|---:|---:|---:|---:|---:|"
    lines = [
        "# Entity linking evaluation",
        "",
        f"Generated {res['timing'].get('generated', datetime.now(UTC).date().isoformat())}. Config `{cfg.version}`.",
        "",
        "## Protocol",
        "",
        f"Data: Mewsli-X WikiNews mentions in 11 languages, fixed-seed sample of {res['n_dev']} dev and "
        f"{res['n_test']} test mentions. Dev fits the combiner, tunes the routing thresholds, and fits "
        "calibration. Test is reported. Every system links a given gold span (disambiguation mode), so "
        "precision and recall are defined over the gold mentions. A mention is out of index when its "
        f"gold entity is absent from BELA's entity index ({res['nil_share']:.1%} of test mentions); the correct "
        "answer for those is NIL. The planned hand-labeled gold set does not exist yet, so adverse-media "
        "metrics, the collision subset from the gold set, and watchlist false-match reduction are not measured.",
        "",
        "Systems: **rapidfuzz** matches the surface to labels and aliases of the evaluation universe in all "
        "13 languages and always answers (no NIL). **bela** takes BELA's top candidate when its score is at least "
        f"{cfg.nil_el}. **hybrid** routes with the tuned thresholds and adjudicates with the cross-encoder "
        "(English) or the local LLM (other languages). **blink alone** re-ranks RapidFuzz candidates with the "
        "cross-encoder, because BLINK's own bi-encoder retriever and its 6 million entity index were not run.",
        "",
        "## Results on test (tuned thresholds)",
        "",
        head,
        *[row(k, v) for k, v in res["tuned"].items()],
        "",
        f"Thresholds: accept at score >= {cfg.accept_el} and margin >= {cfg.accept_margin}, NIL below {cfg.nil_el}, "
        f"adjudication NIL below probability {cfg.adjudication_nil_threshold}. "
        f"With the plan's initial values (0.70, 0.15, 0.40) the hybrid scores F1 {fmt(res['initial']['hybrid']['f1'])} "
        f"and routes {res['initial_routes']['blink'] + res['initial_routes']['llm']:.1%} of mentions to adjudication.",
        "",
        "### English only",
        "",
        head,
        row("rapidfuzz", res["rapidfuzz_en"]),
        row("blink alone (on rapidfuzz candidates)", res["blink_alone_en"]),
        row("bela", res["bela_en"]),
        row("hybrid", res["hybrid_en"]),
        "",
        "### Per language (F1)",
        "",
        "| Language | n | rapidfuzz | bela | hybrid |",
        "|---|---:|---:|---:|---:|",
        *[
            f"| {lang} | {int(res['tuned_by_language']['bela'][lang]['n'])} | "
            f"{fmt(res['tuned_by_language']['rapidfuzz'][lang]['f1'])} | "
            f"{fmt(res['tuned_by_language']['bela'][lang]['f1'])} | "
            f"{fmt(res['tuned_by_language']['hybrid'][lang]['f1'])} |"
            for lang in res["tuned_by_language"]["bela"]
        ],
        "",
        "## Candidate retrieval (recall of the gold entity, in-index mentions)",
        "",
        "| System | recall@1 | recall@5 | recall@10 |",
        "|---|---:|---:|---:|",
        *[
            f"| {k} | {fmt(v['recall@1'])} | {fmt(v['recall@5'])} | {fmt(v['recall@10'])} |"
            for k, v in res["retrieval"].items()
        ],
        "",
        "## Mention detection (BELA, threshold 0.2)",
        "",
        f"The detector finds the annotated span exactly in {res['mention_detection']['exact_recall']:.1%} of passages "
        f"and overlapping it in {res['mention_detection']['overlap_recall']:.1%}. It reports "
        f"{res['mention_detection']['mentions_per_passage']:.1f} mentions per passage; Mewsli-X annotates one, so "
        "detection precision and F1 cannot be computed on this data.",
        "",
        "## Ambiguous and collision subsets",
        "",
        f"Ambiguous (routed to adjudication, n = {res['ambiguous']['n']}): BELA F1 {fmt(res['ambiguous']['bela_f1'])}, "
        f"hybrid F1 {fmt(res['ambiguous']['hybrid_f1'])}, gain {res['ambiguous']['gain_mean']:+.3f} "
        f"(95% paired bootstrap interval {res['ambiguous']['gain_ci95'][0]:+.3f} to {res['ambiguous']['gain_ci95'][1]:+.3f}).",
        "",
        f"Surface collision subset (the folded mention matches labels of at least two entities in the evaluation "
        f"universe; n = {res['collision']['n']}):",
        "",
        head,
        *[row(k, res["collision"][k]) for k in ("rapidfuzz", "bela", "hybrid")],
        "",
        "## Adjudication",
        "",
        f"Mentions routed to adjudication on test: {res['tuned_routes']['blink'] + res['tuned_routes']['llm']:.1%} "
        f"(accept {res['tuned_routes']['accept']:.1%}, NIL {res['tuned_routes']['nil']:.1%}, "
        f"fallback {res['tuned_routes']['fallback']:.1%}).",
        "",
        f"LLM (non-English, gold among top 5, n = {res['llm_adjudication']['n']}): chooses the gold entity "
        f"{fmt(res['llm_adjudication']['accuracy_when_gold_in_top5'])} of the time, against "
        f"{fmt(res['llm_adjudication']['bela_top1_same_rows'])} for BELA's top candidate on the same mentions. "
        f"Unusable LLM answers: {res['llm_adjudication']['failures']}.",
        "",
        f"Cross-encoder (English, gold among top 10, n = {res['blink_adjudication']['n']}): "
        f"{fmt(res['blink_adjudication']['accuracy_when_gold_in_top10'])} against "
        f"{fmt(res['blink_adjudication']['bela_top1_same_rows'])} for BELA's top candidate.",
        "",
        "## Calibration of reported confidence (test)",
        "",
        "| Route | n | ECE raw | ECE after isotonic |",
        "|---|---:|---:|---:|",
        *[
            f"| {k} | {v['n']} | {v['ece_raw']:.3f} | {v['ece_calibrated']:.3f} |"
            for k, v in res["calibration"].items()
        ],
        "",
        "![reliability](reliability.png)",
        "",
        "## Throughput and latency",
        "",
        "```",
        json.dumps(res["timing"], indent=1, default=float),
        "```",
        "",
        "Files: `results.json` (all numbers), `per_language.csv`, `predictions_test.csv`. The website reads `public/metrics.json`.",
        "",
    ]
    return "\n".join(lines)
