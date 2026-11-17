"""Name embeddings for vector retrieval, obtained from the entity linking service."""

from __future__ import annotations

import time

import httpx
import numpy as np

from atlas.api.screening import Embedder
from atlas.config import EMBEDDING_MODEL

COOLDOWN_SECONDS = 30.0


def make_nel_embedder(url: str, model_version: str = EMBEDDING_MODEL) -> Embedder:
    """HTTP embedder with a short timeout and a cooldown after a failure.

    Screening must stay fast when the service is down, so a failure disables vector retrieval
    for 30 seconds instead of adding a timeout to every request.
    """
    state = {"retry_at": 0.0}

    def embed(name: str) -> np.ndarray | None:
        if time.monotonic() < state["retry_at"]:
            return None
        try:
            reply = httpx.post(f"{url}/v1/embed", json={"texts": [name]}, timeout=2.0)
            reply.raise_for_status()
            return np.asarray(reply.json()["embeddings"][0], dtype=np.float32)
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            state["retry_at"] = time.monotonic() + COOLDOWN_SECONDS
            return None

    return Embedder(model_version=model_version, embed=embed)
