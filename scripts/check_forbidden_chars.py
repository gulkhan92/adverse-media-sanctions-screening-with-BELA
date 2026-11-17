"""Fail if the em dash character (U+2014) appears in any tracked text file."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FORBIDDEN = chr(0x2014)


def candidate_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split("\n")
    except (subprocess.CalledProcessError, FileNotFoundError):
        out = [str(p) for p in Path(".").rglob("*") if p.is_file() and ".venv" not in p.parts]
    # Vendored tokenizer data is third-party and must stay byte-exact.
    return [Path(p) for p in out if p and not p.startswith("services/nel/vendor/bela/data/")]


def main() -> int:
    bad: list[str] = []
    for path in candidate_files():
        try:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if FORBIDDEN in line:
                    bad.append(f"{path}:{number}")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable
    for item in bad:
        print(f"forbidden character U+2014 at {item}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
