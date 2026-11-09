"""Download Mewsli-X from Hugging Face and convert it to a common JSONL format.

Output rows: {"text", "start", "end", "qid", "lang"} under data/raw/eval/mewsli_x/.
Licence: Apache-2.0 per the dataset card.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from common import RAW_DIR, get_logger, record_file

SOURCE_KEY = "mewsli_x"
REPO_ID = "izhx/mewsli-x"


MARKED = re.compile(r"\{ (.+?) \}")


def convert_row(row: dict[str, Any], lang: str) -> dict[str, Any] | None:
    """Map one wikinews_mentions row to the common schema.

    Source rows hold ``mention`` as "<surface>: <context with { surface } marked>" and the
    target QID in ``entity_id``. Returns None when the row cannot be parsed.
    """
    raw, qid = row.get("mention"), row.get("entity_id")
    if not raw or not qid:
        return None
    matches = list(MARKED.finditer(raw))
    if not matches:
        return None
    match = matches[-1]
    surface = match.group(1)
    body = raw[: match.start()] + surface + raw[match.end() :]
    prefix = f"{surface}: "
    start = match.start()
    if raw.startswith(prefix):
        body = body[len(prefix) :]
        start -= len(prefix)
    if start < 0 or body[start : start + len(surface)] != surface:
        return None
    end = start + len(surface)
    return {"text": body, "start": start, "end": end, "qid": str(qid), "lang": lang}


def convert_file(path: Path, out_dir: Path) -> tuple[Path, int, int]:
    lang = path.parent.name
    out = out_dir / f"{lang}_{path.stem}.jsonl"
    kept = skipped = 0
    with out.open("w", encoding="utf-8") as sink, path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            converted = convert_row(json.loads(line), lang)
            if converted is None:
                skipped += 1
                continue
            sink.write(json.dumps(converted, ensure_ascii=False) + "\n")
            kept += 1
    return out, kept, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=None, help="pin a dataset revision")
    args = parser.parse_args()
    from huggingface_hub import snapshot_download

    log = get_logger(SOURCE_KEY)
    dest = RAW_DIR / "eval" / "mewsli_x"
    snapshot = Path(
        snapshot_download(
            REPO_ID, repo_type="dataset", local_dir=dest / "hf", revision=args.revision
        )
    )
    out_dir = dest / "jsonl"
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted((snapshot / "wikinews_mentions").rglob("*.jsonl")):
        out, kept, skipped = convert_file(path, out_dir)
        log.info(f"{path.name}: kept={kept} skipped={skipped} -> {out.name}")
        record_file(SOURCE_KEY, out)


if __name__ == "__main__":
    main()
