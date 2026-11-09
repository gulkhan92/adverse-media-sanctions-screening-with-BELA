"""Print and store a row count report for acquired data.

Writes data/reports/row_counts.json and data/reports/row_counts.md.
Counts data rows (CSV header excluded where present, OFAC legacy CSV has none).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from common import DATA_DIR, RAW_DIR, count_lines

HEADERLESS = {"SDN.CSV", "ALT.CSV", "ADD.CSV", "SDN_COMMENTS.CSV"}


def count_rows(path: Path) -> int | None:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        lines = count_lines(path)
        return lines if path.name in HEADERLESS else max(lines - 1, 0)
    if suffix == ".jsonl":
        return count_lines(path)
    if suffix == ".parquet":
        import pyarrow.parquet as pq

        return int(pq.ParquetFile(path).metadata.num_rows)
    return None


def collect() -> dict[str, int]:
    counts: dict[str, int] = {}
    roots = [RAW_DIR, DATA_DIR / "processed", DATA_DIR / "gold"]
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.name.startswith("_"):
                rows = count_rows(path)
                if rows is not None:
                    counts[str(path.relative_to(DATA_DIR))] = rows
    return counts


def main() -> None:
    counts = collect()
    report_dir = DATA_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    (report_dir / "row_counts.json").write_text(
        json.dumps({"generated": generated, "counts": counts}, indent=1, ensure_ascii=False)
    )
    lines = ["# Row counts", "", f"Generated: {generated}", "", "| File | Rows |", "|---|---:|"]
    lines += [f"| {name} | {rows:,} |" for name, rows in counts.items()]
    (report_dir / "row_counts.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
