"""Write apps/web/public/metrics.json from the database and the measurement files in docs/.

Every figure on the marketing site is read from this file. A value that has not been measured
is written as null and the site shows it as pending; nothing is estimated here.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "apps" / "web" / "public" / "metrics.json"


def scalar(sql: str) -> int:
    result = subprocess.run(
        [str(ROOT / "infra" / "dev_db.sh"), "psql", "-Atc", sql],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def latency() -> dict[str, int | None]:
    text = (ROOT / "docs" / "api_latency.md").read_text(encoding="utf-8")
    match = re.search(r"p50: (\d+) ms, p95: (\d+) ms", text)
    return {"p50": int(match[1]), "p95": int(match[2])} if match else {"p50": None, "p95": None}


def evaluation() -> dict[str, object]:
    path = ROOT / "docs" / "eval" / "results.json"
    if not path.exists():
        return {"available": False, "false_match_reduction_pct": None, "systems": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"available": True, **data}


def main() -> None:
    lat = latency()
    metrics = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "languages": {
            "value": 97,
            "source": "BELA model coverage (Plekhanov et al., 2023)",
        },
        "watchlist_entries": {
            "value": scalar("SELECT count(*) FROM watchlist_entries"),
            "source": "Indexed in the development database",
        },
        "latency_ms": {
            **lat,
            "source": "docs/api_latency.md: 150 sequential requests on one machine",
        },
        "evaluation": evaluation(),
        "demo_tenants": scalar("SELECT count(*) FROM tenants"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
