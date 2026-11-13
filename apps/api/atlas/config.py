"""Runtime configuration read from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://atlas@127.0.0.1:54329/atlas")
EMBEDDING_MODEL = os.environ.get("ATLAS_EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = 1024
