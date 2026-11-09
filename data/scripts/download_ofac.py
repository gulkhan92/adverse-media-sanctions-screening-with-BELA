"""Download OFAC SDN and Consolidated list exports (US government, public domain)."""

from __future__ import annotations

import argparse

from common import RAW_DIR, fetch_and_record, get_logger

BASE = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports"
FILES = [
    "SDN.CSV",
    "ALT.CSV",
    "ADD.CSV",
    "SDN_COMMENTS.CSV",
    "SDN_ADVANCED.XML",
    "CONS_ADVANCED.XML",
]
SOURCE_KEY = "ofac"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="re-download even if current")
    args = parser.parse_args()
    log = get_logger(SOURCE_KEY)
    dest_dir = RAW_DIR / "sanctions" / "ofac"
    for name in FILES:
        fetch_and_record(SOURCE_KEY, f"{BASE}/{name}", dest_dir / name, force=args.force)
    log.info("ofac download complete")


if __name__ == "__main__":
    main()
