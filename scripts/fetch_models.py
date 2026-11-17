"""Download model files with resume support and record SHA-256 hashes in models/manifest.yaml.

Usage: python scripts/fetch_models.py [bela-small|bela-index|blink|llm|all]

  bela-small  BELA checkpoints (wiki, mewsli) and entity id list (MIT, wannaphong/BELA).
  bela-index  BELA entity embeddings (about 19.8 GB). Required by the nel service.
  blink       BLINK cross-encoder weights and config (MIT, dl.fbaipublicfiles.com/BLINK).
  llm         Qwen3-4B-Instruct-2507 GGUF Q4_K_M (Apache-2.0, unsloth build).
"""

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
MANIFEST = MODELS / "manifest.yaml"
HF = "https://huggingface.co"
GROUPS: dict[str, list[tuple[str, str, str]]] = {
    "bela-small": [
        ("bela", f"{HF}/wannaphong/BELA/resolve/main/model_wiki.ckpt", "bela/model_wiki.ckpt"),
        ("bela", f"{HF}/wannaphong/BELA/resolve/main/model_mewsli.ckpt", "bela/model_mewsli.ckpt"),
        ("bela", f"{HF}/wannaphong/BELA/resolve/main/index.txt", "bela/index.txt"),
    ],
    "bela-index": [
        ("bela", f"{HF}/wannaphong/BELA/resolve/main/embeddings.pt", "bela/embeddings.pt"),
    ],
    "blink": [
        (
            "blink",
            "https://dl.fbaipublicfiles.com/BLINK/crossencoder_wiki_large.bin",
            "blink/crossencoder_wiki_large.bin",
        ),
        (
            "blink",
            "https://dl.fbaipublicfiles.com/BLINK/crossencoder_wiki_large.json",
            "blink/crossencoder_wiki_large.json",
        ),
    ],
    "llm": [
        (
            "qwen3",
            f"{HF}/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/"
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            "llm/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        ),
    ],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 24):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(8):
        offset = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with httpx.stream(
                "GET", url, headers=headers, follow_redirects=True, timeout=60
            ) as response:
                if response.status_code == 416:
                    break
                response.raise_for_status()
                mode = "ab" if offset and response.status_code == 206 else "wb"
                with part.open(mode) as handle:
                    for block in response.iter_bytes(1 << 22):
                        handle.write(block)
            break
        except httpx.HTTPError as exc:
            print(f"retry {attempt + 1} for {dest.name}: {type(exc).__name__}", flush=True)
    part.rename(dest)


def main() -> None:
    wanted = sys.argv[1] if len(sys.argv) > 1 else "all"
    groups = list(GROUPS) if wanted == "all" else [wanted]
    manifest = yaml.safe_load(MANIFEST.read_text()) if MANIFEST.exists() else {}
    manifest = manifest or {}
    for group in groups:
        for name, url, rel in GROUPS[group]:
            dest = MODELS / rel
            recorded = manifest.get(rel)
            if dest.exists() and recorded and recorded["size_bytes"] == dest.stat().st_size:
                print(f"skip {rel}")
                continue
            print(f"fetch {rel}", flush=True)
            fetch(url, dest)
            manifest[rel] = {
                "model": name,
                "url": url,
                "size_bytes": dest.stat().st_size,
                "sha256": sha256(dest),
                "retrieved": datetime.now(UTC).date().isoformat(),
            }
            MANIFEST.write_text(yaml.safe_dump(manifest, sort_keys=True))
            print(f"done {rel} {manifest[rel]['sha256'][:12]}", flush=True)


if __name__ == "__main__":
    main()
