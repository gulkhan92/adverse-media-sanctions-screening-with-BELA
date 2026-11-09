"""Download the ICIJ Offshore Leaks Database CSV archive.

The archive location is published on https://offshoreleaks.icij.org/pages/database.
The direct link changes over time, so the URL can be overridden with --url or the
ICIJ_ARCHIVE_URL environment variable. The default is the documented bulk archive.
Licence: database under ODbL, contents under CC BY-SA. Cite ICIJ.
"""

from __future__ import annotations

import argparse
import os
import zipfile

from common import RAW_DIR, fetch_and_record, get_logger

SOURCE_KEY = "icij"
DEFAULT_URL = "https://offshoreleaks-data.icij.org/offshoreleaks/csv/full-oldb.LATEST.zip"
EXPECTED = [
    "nodes-entities.csv",
    "nodes-officers.csv",
    "nodes-intermediaries.csv",
    "nodes-addresses.csv",
    "nodes-others.csv",
    "relationships.csv",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("ICIJ_ARCHIVE_URL", DEFAULT_URL))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    log = get_logger(SOURCE_KEY)
    dest_dir = RAW_DIR / "leaks" / "icij"
    archive = fetch_and_record(SOURCE_KEY, args.url, dest_dir / "full-oldb.zip", force=args.force)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(dest_dir)
    missing = [name for name in EXPECTED if not (dest_dir / name).exists()]
    if missing:
        log.warning(f"expected files not found after extraction: {missing}")
    log.info("icij download complete")


if __name__ == "__main__":
    main()
