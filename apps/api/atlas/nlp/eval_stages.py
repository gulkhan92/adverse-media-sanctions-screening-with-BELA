"""Model-facing evaluation stages with on-disk caches (one JSONL file per stage).

Stages are restartable: a record that is already in a stage file is not recomputed. The
analysis in ``eval_analysis`` reads these files and needs no models.
"""

from __future__ import annotations

import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from atlas.config import DATA_DIR, REPO_ROOT
from atlas.names import fold
from atlas.nlp.adjudicate import Candidate, blink_probabilities, llm_choose
from atlas.nlp.entity_info import EntityInfoStore
from atlas.nlp.llm_client import LlmClient, LlmUnavailable

MEWSLI = DATA_DIR / "raw" / "eval" / "mewsli_x" / "jsonl"
EVAL_DIR = DATA_DIR / "processed" / "eval"
LANGS = ["ar", "de", "en", "es", "fa", "ja", "pl", "ro", "ta", "tr", "uk"]
INFO_LANGS = (
    "en",
    "ar",
    "de",
    "es",
    "fa",
    "fr",
    "ja",
    "pl",
    "ro",
    "ru",
    "ta",
    "tr",
    "uk",
)
for extra in ("services/nel", "services/blink"):
    path = str(REPO_ROOT / extra)
    if path not in sys.path:
        sys.path.insert(0, path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_sample(dev_per_lang: int = 100, test_per_lang: int = 200, seed: int = 42) -> Path:
    """Fixed-seed sample of Mewsli-X mentions: dev for calibration, test for reporting."""
    path = EVAL_DIR / "sample.jsonl"
    if path.exists():
        return path
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for lang in LANGS:
        for split, size in (("dev", dev_per_lang), ("test", test_per_lang)):
            source = read_jsonl(MEWSLI / f"{lang}_{split}.jsonl")
            chosen = rng.sample(source, min(size, len(source)))
            for i, r in enumerate(chosen):
                rows.append(
                    {
                        "id": f"{lang}-{split}-{i}",
                        "lang": lang,
                        "split": split,
                        "text": r["text"],
                        "start": r["start"],
                        "end": r["end"],
                        "gold": r["qid"],
                    }
                )
    append_jsonl(path, rows)
    return path


def stage_bela(batch: int = 16) -> dict[str, Any]:
    """Candidates for the gold span (disambiguation mode) and detected spans (end to end)."""
    from atlas_nel.bela_linker import BelaLinker

    sample = read_jsonl(EVAL_DIR / "sample.jsonl")
    out_path = EVAL_DIR / "bela.jsonl"
    done = {r["id"] for r in read_jsonl(out_path)}
    todo = [r for r in sample if r["id"] not in done]
    models = REPO_ROOT / "models" / "bela"
    linker = BelaLinker(models / "model_wiki.ckpt", models / "embeddings.pt", models / "index.txt")
    index = set(linker.entity_ids)
    latencies: list[float] = []
    for i in range(0, len(todo), batch):
        part = todo[i : i + batch]
        texts = [r["text"] for r in part]
        started = time.perf_counter()
        spans = linker.link_spans(texts, [(r["start"], r["end"]) for r in part])
        detected = linker.link(texts)
        latencies.append(time.perf_counter() - started)
        rows = []
        for r, span, found in zip(part, spans, detected, strict=True):
            rows.append(
                {
                    "id": r["id"],
                    "in_kb": r["gold"] in index,
                    "aligned": span is not None,
                    "cands": span.candidates if span else [],
                    "detected": [
                        {"start": m.start, "end": m.end, "md": m.md_score, "top": m.candidates[:1]}
                        for m in found
                    ],
                }
            )
        append_jsonl(out_path, rows)
        print(f"bela {i + len(part)}/{len(todo)}", flush=True)
    return {"batches": len(latencies), "batch_size": batch, "latencies": latencies}


def stage_info() -> int:
    """Titles, descriptions, and multilingual labels for gold and candidate QIDs."""
    qids: set[str] = {r["gold"] for r in read_jsonl(EVAL_DIR / "sample.jsonl")}
    for r in read_jsonl(EVAL_DIR / "bela.jsonl"):
        qids |= {c["qid"] for c in r["cands"]}
    store = EntityInfoStore(EVAL_DIR / "entity_info.jsonl", languages=INFO_LANGS)
    return store.ensure(sorted(qids))


def load_info() -> EntityInfoStore:
    return EntityInfoStore(EVAL_DIR / "entity_info.jsonl", languages=INFO_LANGS)


def stage_fuzzy() -> int:
    """RapidFuzz name matching over the labels and aliases of the evaluation universe."""
    store = load_info()
    out_path = EVAL_DIR / "fuzzy.jsonl"
    done = {r["id"] for r in read_jsonl(out_path)}
    choices: list[str] = []
    owners: list[str] = []
    for qid, info in store.items.items():
        strings = set(info.labels.values())
        for values in info.aliases.values():
            strings.update(values)
        for text in strings:
            folded = fold(text)
            if folded:
                choices.append(folded)
                owners.append(qid)
    rows = []
    sample = [r for r in read_jsonl(EVAL_DIR / "sample.jsonl") if r["id"] not in done]
    for n, r in enumerate(sample, start=1):
        surface = fold(r["text"][r["start"] : r["end"]])
        hits = process.extract(surface, choices, scorer=fuzz.WRatio, limit=60, processor=None)
        ranked: list[dict[str, Any]] = []
        for _, score, idx in hits:
            qid = owners[idx]
            if all(c["qid"] != qid for c in ranked):
                ranked.append({"qid": qid, "score": score / 100.0})
            if len(ranked) == 10:
                break
        rows.append({"id": r["id"], "cands": ranked})
        if n % 200 == 0:
            append_jsonl(out_path, rows)
            rows = []
            print(f"fuzzy {n}/{len(sample)}", flush=True)
    append_jsonl(out_path, rows)
    return len(sample)


def _candidates(store: EntityInfoStore, raw: list[dict[str, Any]], limit: int) -> list[Candidate]:
    out = []
    for c in raw[:limit]:
        meta = store.get(c["qid"])
        out.append(Candidate(c["qid"], meta.title, meta.description, float(c["score"])))
    return out


def stage_blink() -> int:
    """Cross-encoder probabilities for English mentions over BELA and fuzzy candidates."""
    from atlas_blink.crossencoder import BlinkCrossEncoder

    store = load_info()
    models = REPO_ROOT / "models" / "blink"
    model = BlinkCrossEncoder(
        models / "crossencoder_wiki_large.bin", models / "crossencoder_wiki_large.json"
    )
    sample = {r["id"]: r for r in read_jsonl(EVAL_DIR / "sample.jsonl") if r["lang"] == "en"}
    bela = {r["id"]: r for r in read_jsonl(EVAL_DIR / "bela.jsonl")}
    fuzzy = {r["id"]: r for r in read_jsonl(EVAL_DIR / "fuzzy.jsonl")}
    out_path = EVAL_DIR / "blink.jsonl"
    done = {r["id"] for r in read_jsonl(out_path)}
    rows = []
    started = time.perf_counter()
    for n, (mid, r) in enumerate(sample.items(), start=1):
        if mid in done or mid not in bela:
            continue
        left, mention, right = (
            r["text"][: r["start"]][-220:],
            r["text"][r["start"] : r["end"]],
            r["text"][r["end"] :][:220],
        )
        entry: dict[str, Any] = {"id": mid}
        for key, raw in (
            ("bela", bela[mid]["cands"]),
            ("fuzzy", fuzzy.get(mid, {}).get("cands", [])),
        ):
            cands = _candidates(store, raw, 10)
            entry[key] = blink_probabilities(model, left, mention, right, cands) if cands else {}
        rows.append(entry)
        if n % 50 == 0:
            print(f"blink {n}/{len(sample)}", flush=True)
    append_jsonl(out_path, rows)
    return len(rows) and int((time.perf_counter() - started) * 1000 / len(rows))


def stage_llm(workers: int = 2, min_top: float = 0.25) -> int:
    """LLM choices for non-English mentions that are not already very confident."""
    store = load_info()
    client = LlmClient(max_tokens=160)
    sample = [r for r in read_jsonl(EVAL_DIR / "sample.jsonl") if r["lang"] != "en"]
    bela = {r["id"]: r for r in read_jsonl(EVAL_DIR / "bela.jsonl")}
    out_path = EVAL_DIR / "llm.jsonl"
    done = {r["id"] for r in read_jsonl(out_path)}
    jobs = []
    for r in sample:
        b = bela.get(r["id"])
        if r["id"] in done or not b or not b["cands"]:
            continue
        scores = sorted(c["score"] for c in b["cands"])[::-1]
        top, margin = scores[0], scores[0] - (scores[1] if len(scores) > 1 else 0)
        if top < min_top or (top >= 0.85 and margin >= 0.30):
            continue  # clearly NIL or clearly accepted under any plausible threshold
        jobs.append((r, b))

    def run(job: tuple[dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
        r, b = job
        cands = _candidates(store, b["cands"], 5)
        window = r["text"][max(0, r["start"] - 220) : r["end"] + 220]
        try:
            qid, conf, reason = llm_choose(client, window, r["text"][r["start"] : r["end"]], cands)
            return {"id": r["id"], "choice": qid, "confidence": conf, "ok": True}
        except LlmUnavailable:
            return {"id": r["id"], "choice": None, "confidence": 0.0, "ok": False}

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(workers) as pool:
        for n, result in enumerate(pool.map(run, jobs), start=1):
            rows.append(result)
            if n % 100 == 0:
                append_jsonl(out_path, rows)
                rows = []
                print(f"llm {n}/{len(jobs)}", flush=True)
    append_jsonl(out_path, rows)
    return len(jobs)
