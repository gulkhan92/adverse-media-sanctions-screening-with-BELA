"""Verify that every file listed in manifest.yaml exists and matches its SHA-256 hash."""

from __future__ import annotations

import sys

from common import DATA_DIR, load_manifest, sha256_file


def verify() -> list[str]:
    problems: list[str] = []
    for key, source in sorted(load_manifest()["sources"].items()):
        for rel, entry in sorted(source.get("files", {}).items()):
            path = DATA_DIR / rel
            if not path.exists():
                problems.append(f"{key}: missing {rel}")
            elif path.stat().st_size != entry["size_bytes"]:
                problems.append(f"{key}: size mismatch {rel}")
            elif sha256_file(path) != entry["sha256"]:
                problems.append(f"{key}: hash mismatch {rel}")
    return problems


def main() -> None:
    problems = verify()
    for problem in problems:
        print(f"FAIL: {problem}")
    if problems:
        sys.exit(1)
    print("OK: all manifest entries verified")


if __name__ == "__main__":
    main()
