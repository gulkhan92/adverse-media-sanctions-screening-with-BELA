"""Check the GDELT harvester health: cycles ran without failure over a time window.

Exit criterion for phase 1: harvester has run for 48 hours without failure.
A failure is a cycle in which every query errored, or a gap between consecutive
cycles longer than --max-gap-minutes.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from harvest_gdelt import STATUS_PATH


def evaluate(history: list[dict[str, object]], hours: float, max_gap_minutes: float) -> list[str]:
    now = datetime.now(UTC)
    window = [
        h for h in history
        if datetime.fromisoformat(str(h["finished"])) >= now - timedelta(hours=hours)
    ]
    problems: list[str] = []
    if not window:
        return ["no cycles recorded in the window"]
    first = datetime.fromisoformat(str(window[0]["started"]))
    if first > now - timedelta(hours=hours) + timedelta(minutes=max_gap_minutes):
        problems.append(f"harvester history covers less than {hours} hours")
    previous_end: datetime | None = None
    for cycle in window:
        start = datetime.fromisoformat(str(cycle["started"]))
        if int(str(cycle["errors"])) >= int(str(cycle["queries"])):
            problems.append(f"cycle at {cycle['started']} failed on every query")
        if previous_end and start - previous_end > timedelta(minutes=max_gap_minutes):
            problems.append(f"gap before cycle at {cycle['started']}")
        previous_end = datetime.fromisoformat(str(cycle["finished"]))
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=48)
    parser.add_argument("--max-gap-minutes", type=float, default=90)
    args = parser.parse_args()
    if not STATUS_PATH.exists():
        raise SystemExit("no harvester status file found")
    problems = evaluate(json.loads(STATUS_PATH.read_text()), args.hours, args.max_gap_minutes)
    for problem in problems:
        print(f"FAIL: {problem}")
    if problems:
        raise SystemExit(1)
    print(f"OK: harvester healthy over the last {args.hours} hours")


if __name__ == "__main__":
    main()
