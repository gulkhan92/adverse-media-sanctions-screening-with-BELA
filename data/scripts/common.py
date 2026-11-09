"""Shared helpers for data acquisition scripts.

Provides resumable downloads, SHA-256 hashing, structured logging, and
manifest updates. All scripts are idempotent: re-running them skips files
whose recorded hash still matches the file on disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from tenacity import retry, stop_after_attempt, wait_exponential

DATA_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MANIFEST_PATH = DATA_DIR / "manifest.yaml"
CONTACT = os.environ.get("ATLAS_CONTACT", "set-ATLAS_CONTACT-env-var")
USER_AGENT = f"AtlasScreeningResearchBot/0.1 (contact: {CONTACT})"
CHUNK = 1024 * 1024


def get_logger(name: str) -> logging.Logger:
    """Return a logger that writes one JSON object per line to stderr."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"sources": {}}
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    data.setdefault("sources", {})
    return data  # type: ignore[no-any-return]


def save_manifest(manifest: dict[str, Any]) -> None:
    with MANIFEST_PATH.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(manifest, handle, sort_keys=True, allow_unicode=True)


def record_file(source_key: str, path: Path) -> dict[str, Any]:
    """Record size, hash, and retrieval date of a downloaded file in the manifest."""
    manifest = load_manifest()
    source = manifest["sources"].setdefault(source_key, {})
    files = source.setdefault("files", {})
    rel = str(path.relative_to(DATA_DIR))
    files[rel] = {
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "retrieved": datetime.now(UTC).date().isoformat(),
    }
    save_manifest(manifest)
    return files[rel]  # type: ignore[no-any-return]


def file_is_current(source_key: str, path: Path) -> bool:
    """True when the file exists and matches the hash stored in the manifest."""
    if not path.exists():
        return False
    entry = load_manifest()["sources"].get(source_key, {}).get("files", {}).get(
        str(path.relative_to(DATA_DIR))
    )
    return bool(entry and entry["sha256"] == sha256_file(path))


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=2, max=60), reraise=True)
def download(url: str, dest: Path, *, timeout: float = 120.0) -> Path:
    """Download url to dest with resume support.

    Data is written to ``dest.part`` and renamed on success. If a partial
    file exists, an HTTP Range request continues from its current size.
    """
    log = get_logger("download")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    with httpx.stream(
        "GET", url, headers=headers, timeout=timeout, follow_redirects=True
    ) as response:
        if response.status_code == 416:  # already complete
            part.rename(dest)
            return dest
        response.raise_for_status()
        mode = "ab" if offset and response.status_code == 206 else "wb"
        with part.open(mode) as handle:
            for block in response.iter_bytes(CHUNK):
                handle.write(block)
    part.rename(dest)
    log.info(f"downloaded {url} -> {dest} ({dest.stat().st_size} bytes)")
    return dest


def fetch_and_record(source_key: str, url: str, dest: Path, *, force: bool = False) -> Path:
    """Download url unless the file is current, then record it in the manifest."""
    log = get_logger(source_key)
    if not force and file_is_current(source_key, dest):
        log.info(f"skip {dest.name}: hash matches manifest")
        return dest
    download(url, dest)
    entry = record_file(source_key, dest)
    log.info(f"recorded {dest.name} sha256={entry['sha256'][:12]}")
    return dest


def count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(block.count(b"\n") for block in iter(lambda: handle.read(CHUNK), b""))
