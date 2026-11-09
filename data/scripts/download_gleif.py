"""Download the GLEIF Golden Copy: Level 1 (LEI records) and Level 2 (relationships), CSV."""

from __future__ import annotations

import argparse
import zipfile

import httpx
from common import RAW_DIR, USER_AGENT, fetch_and_record, get_logger

SOURCE_KEY = "gleif"
LATEST = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest"
# Keys in the publish metadata: lei2 = Level 1, rr = Level 2 relationship records.
KINDS = {"lei2": "level1_lei_records", "rr": "level2_relationships"}


def resolve_urls() -> dict[str, str]:
    response = httpx.get(LATEST, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    data = response.json()["data"]
    return {kind: data[kind]["full_file"]["csv"]["url"] for kind in KINDS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-extract", action="store_true", help="keep zip files only")
    args = parser.parse_args()
    log = get_logger(SOURCE_KEY)
    dest_dir = RAW_DIR / "registry" / "gleif"
    for kind, url in resolve_urls().items():
        dest = dest_dir / f"{KINDS[kind]}.csv.zip"
        fetch_and_record(SOURCE_KEY, url, dest, force=args.force)
        if not args.no_extract:
            with zipfile.ZipFile(dest) as archive:
                archive.extractall(dest_dir)
            log.info(f"extracted {dest.name}")


if __name__ == "__main__":
    main()
