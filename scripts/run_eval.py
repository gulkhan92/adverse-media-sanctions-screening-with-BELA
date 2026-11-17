"""Run the entity linking evaluation. Usage: python scripts/run_eval.py [stage ...]

Stages (each is cached and restartable): sample, bela, info, fuzzy, blink, llm, timing, analyze.
With no arguments all stages run in order. The llm stage needs llama-server running
(services/llm/run_server.sh); bela and blink need the files from scripts/fetch_models.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from atlas.nlp import eval_analysis, eval_stages  # noqa: E402
from atlas.nlp.metrics import percentile  # noqa: E402

TIMING = eval_stages.EVAL_DIR / "timing.json"
ORDER = ["sample", "bela", "info", "fuzzy", "blink", "llm", "timing", "analyze"]


def stage_timing() -> None:
    """Throughput and latency of the BELA stage on short passages and on 256-token chunks."""
    import time

    from atlas_nel.bela_linker import BelaLinker

    models = ROOT / "models" / "bela"
    linker = BelaLinker(models / "model_wiki.ckpt", models / "embeddings.pt", models / "index.txt")
    sample = eval_stages.read_jsonl(eval_stages.EVAL_DIR / "sample.jsonl")
    texts = [r["text"] for r in sample if r["split"] == "test"][:256]
    long_texts = [" ".join(texts[i : i + 6]) for i in range(0, 192, 6)]
    result: dict[str, object] = {"device": str(linker.device)}
    for name, items in (("short_passages", texts), ("chunks_256_tokens", long_texts)):
        for batch in (8, 32):
            linker.link(items[:batch])  # warm-up
            latencies = []
            started = time.perf_counter()
            for i in range(0, len(items) - batch + 1, batch):
                t = time.perf_counter()
                linker.link(items[i : i + batch])
                latencies.append(time.perf_counter() - t)
            total = time.perf_counter() - started
            done = len(latencies) * batch
            result[f"{name}_batch{batch}"] = {
                "passages_per_second": round(done / total, 2),
                "batch_latency_p50_s": round(percentile(latencies, 0.5), 3),
                "batch_latency_p95_s": round(percentile(latencies, 0.95), 3),
            }
    TIMING.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


def main() -> None:
    stages = sys.argv[1:] or ORDER
    for stage in stages:
        print(f"== {stage}", flush=True)
        if stage == "sample":
            print(eval_stages.build_sample())
        elif stage == "bela":
            eval_stages.stage_bela()
        elif stage == "info":
            print("fetched", eval_stages.stage_info())
        elif stage == "fuzzy":
            eval_stages.stage_fuzzy()
        elif stage == "blink":
            print("ms per mention", eval_stages.stage_blink())
        elif stage == "llm":
            print("jobs", eval_stages.stage_llm())
        elif stage == "timing":
            stage_timing()
        elif stage == "analyze":
            timing = json.loads(TIMING.read_text()) if TIMING.exists() else {}
            eval_analysis.analyze(timing)
            print("report written to docs/eval/report.md")
        else:
            raise SystemExit(f"unknown stage {stage}; choose from {ORDER}")


if __name__ == "__main__":
    main()
