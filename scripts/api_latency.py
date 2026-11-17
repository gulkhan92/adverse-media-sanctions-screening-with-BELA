"""Measure single-screening latency against a running API (writes docs/api_latency.md).

Usage: python scripts/api_latency.py --base http://127.0.0.1:8090 --email E --password P
Names are sampled from the loaded watchlist (exact, reordered, and misspelled variants), so the
query mix is realistic. Results are recorded for the performance report in phase 9.
"""

from __future__ import annotations

import argparse
import random
import statistics
import time
from pathlib import Path

import httpx
import psycopg

OUT = Path(__file__).resolve().parents[1] / "docs" / "api_latency.md"


def variants(name: str, rng: random.Random) -> list[str]:
    parts = name.split()
    out = [name, " ".join(reversed(parts))]
    if len(name) > 6:
        i = rng.randrange(1, len(name) - 1)
        out.append(name[:i] + name[i + 1 :])
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8090")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--n", type=int, default=150)
    parser.add_argument("--db", default="postgresql://atlas@127.0.0.1:54329/atlas")
    args = parser.parse_args()
    rng = random.Random(7)
    with psycopg.connect(args.db) as conn:
        rows = conn.execute(
            "SELECT name, kind FROM watchlist_entries WHERE kind IN ('person', 'organization') "
            "ORDER BY random() LIMIT %s",
            (args.n,),
        ).fetchall()
        total = conn.execute("SELECT count(*) FROM watchlist_entries").fetchone()[0]  # type: ignore[index]
    with httpx.Client(base_url=args.base, timeout=30) as client:
        login = client.post(
            "/api/v1/auth/login", json={"email": args.email, "password": args.password}
        )
        login.raise_for_status()
        headers = {"X-CSRF-Token": client.cookies["atlas_csrf"]}
        times: list[float] = []
        for name, kind in rows:
            query = rng.choice(variants(name, rng))
            started = time.perf_counter()
            reply = client.post(
                "/api/v1/screenings",
                json={"subject": {"name": query, "kind": kind}},
                headers=headers,
            )
            times.append((time.perf_counter() - started) * 1000)
            reply.raise_for_status()
    times.sort()
    p50 = statistics.median(times)
    p95 = times[int(0.95 * (len(times) - 1))]
    OUT.write_text(
        "# Single screening latency\n\n"
        f"- Requests: {len(times)} sequential `POST /api/v1/screenings` calls, one client\n"
        f"- Watchlist entries loaded: {total}\n"
        f"- p50: {p50:.0f} ms, p95: {p95:.0f} ms, max: {times[-1]:.0f} ms\n"
        "- Query mix: exact, word-reversed, and one-character-deleted names sampled from the list\n"
        "- Environment: development laptop, API, PostgreSQL, and client on one machine\n",
        encoding="utf-8",
    )
    print(f"p50={p50:.0f}ms p95={p95:.0f}ms n={len(times)}")


if __name__ == "__main__":
    main()
