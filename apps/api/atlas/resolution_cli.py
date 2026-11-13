"""Command line entry point: resolve watchlist entries against the knowledge base."""

from __future__ import annotations

import argparse
import json

from atlas.db import connect
from atlas.resolution import resolve


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-embeddings", action="store_true")
    args = parser.parse_args()
    with connect() as conn:
        print(json.dumps(resolve(conn, use_embeddings=not args.no_embeddings), indent=1))


if __name__ == "__main__":
    main()
