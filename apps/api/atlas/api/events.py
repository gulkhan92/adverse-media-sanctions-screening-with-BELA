"""Tenant-scoped event bus for Server-Sent Events: Valkey pub/sub or an in-process fallback."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from typing import Any


def channel(tenant_id: str) -> str:
    return f"alerts:{tenant_id}"


class MemoryBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[str]]]] = {}

    def publish(self, tenant_id: str, event: dict[str, Any]) -> None:
        payload = json.dumps(event, default=str)
        with self._lock:
            subs = list(self._subs.get(channel(tenant_id), []))
        for loop, queue in subs:
            loop.call_soon_threadsafe(queue.put_nowait, payload)

    async def subscribe(self, tenant_id: str) -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        entry = (asyncio.get_running_loop(), queue)
        name = channel(tenant_id)
        with self._lock:
            self._subs.setdefault(name, []).append(entry)
        try:
            while True:
                yield await queue.get()
        finally:
            with self._lock:
                self._subs[name].remove(entry)


class ValkeyBus:
    def __init__(self, url: str) -> None:
        import valkey
        import valkey.asyncio as avalkey

        self.url = url
        self.sync = valkey.Valkey.from_url(url)
        self._avalkey = avalkey

    def publish(self, tenant_id: str, event: dict[str, Any]) -> None:
        self.sync.publish(channel(tenant_id), json.dumps(event, default=str))

    async def subscribe(self, tenant_id: str) -> AsyncIterator[str]:
        client = self._avalkey.Valkey.from_url(self.url)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel(tenant_id))
        try:
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    data = message["data"]
                    yield data.decode() if isinstance(data, bytes) else str(data)
        finally:
            await pubsub.unsubscribe(channel(tenant_id))
            await pubsub.aclose()
            await client.aclose()
