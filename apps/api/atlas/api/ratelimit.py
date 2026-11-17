"""Fixed-window rate limiter with an in-process backend and an optional Valkey backend."""

from __future__ import annotations

import threading
import time
from typing import Any

from atlas.api.errors import too_many


class MemoryBackend:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[int, float]] = {}

    def hit(self, key: str, window: int) -> tuple[int, int]:
        """Return (count in the current window, seconds until the window ends)."""
        now = time.monotonic()
        with self._lock:
            count, started = self._hits.get(key, (0, now))
            if now - started >= window:
                count, started = 0, now
            count += 1
            self._hits[key] = (count, started)
            if len(self._hits) > 50_000:
                self._hits = {k: v for k, v in self._hits.items() if now - v[1] < window}
            return count, max(1, int(window - (now - started)))

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class ValkeyBackend:
    def __init__(self, client: Any) -> None:
        self.client = client

    def hit(self, key: str, window: int) -> tuple[int, int]:
        name = f"rl:{key}"
        pipe = self.client.pipeline()
        pipe.incr(name)
        pipe.expire(name, window, nx=True)
        pipe.ttl(name)
        count, _, ttl = pipe.execute()
        return int(count), max(1, int(ttl))

    def reset(self) -> None:
        for name in self.client.scan_iter("rl:*"):
            self.client.delete(name)


class RateLimiter:
    def __init__(self, backend: MemoryBackend | ValkeyBackend | None = None) -> None:
        self.backend = backend or MemoryBackend()

    def check(self, key: str, limit: tuple[int, int]) -> None:
        count, retry = self.backend.hit(key, limit[1])
        if count > limit[0]:
            raise too_many(retry)

    def reset(self) -> None:
        self.backend.reset()
