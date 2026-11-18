"""Small in-process response cache with a time to live, used for analytics responses.

Keys include the tenant scope, so one tenant's cached result is never served to another.
The cache is per process; a refresh of the business views clears it.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any


class ResponseCache:
    def __init__(self, ttl_seconds: float = 60.0, max_entries: int = 512) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._items: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any) -> Any | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < time.monotonic():
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return value

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            self._items[key] = (time.monotonic() + self.ttl, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
