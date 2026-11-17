"""Throughput test: push passages through a running nel service and report rates.

Usage: python scripts/throughput_test.py [--passages 10000] [--url http://127.0.0.1:8082]

Passages are Mewsli-X texts (short news sentences). Results go to docs/eval/throughput.md.
The test passes when every passage is processed without a failed request.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
MEWSLI = ROOT / "data" / "raw" / "eval" / "mewsli_x" / "jsonl"


def load_passages(limit: int) -> list[dict[str, str]]:
    texts: list[tuple[str, str]] = []
    for path in sorted(MEWSLI.glob("*_test.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                texts.append((row["lang"], row["text"]))
    # Interleave languages so every batch is mixed, then cycle if more are requested.
    texts.sort(key=lambda t: hash(t[1]) % 100003)
    out = []
    for i in range(limit):
        lang, text = texts[i % len(texts)]
        out.append({"id": f"p{i}", "text": text, "lang": lang})
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--passages", type=int, default=10000)
    parser.add_argument("--url", default="http://127.0.0.1:8082")
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--clients", type=int, default=2)
    args = parser.parse_args()
    passages = load_passages(args.passages)
    batches = [passages[i : i + args.batch] for i in range(0, len(passages), args.batch)]
    latencies: list[float] = []
    failures = 0
    mentions = 0

    def send(batch: list[dict[str, str]]) -> tuple[float, int, bool]:
        for attempt in range(6):
            started = time.perf_counter()
            try:
                r = httpx.post(f"{args.url}/v1/link", json={"passages": batch}, timeout=120)
                if r.status_code == 503:  # backpressure: wait and retry
                    time.sleep(1 + attempt)
                    continue
                r.raise_for_status()
                found = sum(len(p["mentions"]) for p in r.json()["results"])
                return time.perf_counter() - started, found, True
            except httpx.HTTPError:
                time.sleep(1)
        return 0.0, 0, False

    started = time.perf_counter()
    with ThreadPoolExecutor(args.clients) as pool:
        for n, (latency, found, ok) in enumerate(pool.map(send, batches), start=1):
            if ok:
                latencies.append(latency)
                mentions += found
            else:
                failures += 1
            if n % 50 == 0:
                print(f"{n * args.batch}/{len(passages)} passages", flush=True)
    elapsed = time.perf_counter() - started
    ordered = sorted(latencies)
    p95 = ordered[int(0.95 * (len(ordered) - 1))] if ordered else float("nan")
    result = {
        "passages": len(passages),
        "failed_batches": failures,
        "seconds": round(elapsed, 1),
        "passages_per_second": round(len(passages) / elapsed, 2),
        "mentions_found": mentions,
        "batch_size": args.batch,
        "clients": args.clients,
        "batch_latency_p50_s": round(statistics.median(latencies), 3) if latencies else None,
        "batch_latency_p95_s": round(p95, 3),
        "passed": failures == 0,
    }
    print(json.dumps(result, indent=1))
    out = ROOT / "docs" / "eval" / "throughput.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# Throughput test\n\n"
        f"Run {datetime.now(UTC).isoformat(timespec='seconds')} against the nel service "
        "(BELA, `model_wiki`, index resident on the GPU in half precision). Passages are "
        "Mewsli-X news sentences, not full articles.\n\n```\n"
        + json.dumps(result, indent=1)
        + "\n```\n"
    )
    raise SystemExit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
