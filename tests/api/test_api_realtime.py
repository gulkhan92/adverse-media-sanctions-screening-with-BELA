"""Server-Sent Events over a real HTTP server, and the Valkey backends."""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from api_data import populate

VALKEY_URL = os.environ.get("ATLAS_TEST_VALKEY", "redis://127.0.0.1:6390/0")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def server(seed: dict[str, Any]) -> Iterator[str]:
    port = _free_port()
    config = uvicorn.Config(
        seed["app"], host="127.0.0.1", port=port, log_level="error", lifespan="off"
    )
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(100):
        if instance.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    instance.should_exit = True
    thread.join(timeout=5)


def _cookie_header(client: Any) -> dict[str, str]:
    names = ("atlas_access",)
    return {
        "Cookie": "; ".join(f"{c.name}={c.value}" for c in client.cookies.jar if c.name in names)
    }


def _read_events(url: str, headers: dict[str, str], out: list[str], ready: threading.Event) -> None:
    try:
        with httpx.stream("GET", url, headers=headers, timeout=6) as reply:
            assert reply.status_code == 200
            assert reply.headers["content-type"].startswith("text/event-stream")
            ready.set()
            for line in reply.iter_lines():
                out.append(line)
                if line.startswith("data:"):
                    return
    except httpx.ReadTimeout:
        return  # the other tenant's stream receives nothing, which is the expected outcome


def test_sse_delivers_only_to_the_tenant(
    server: str, make_client: Any, seed: dict[str, Any]
) -> None:
    ids = populate(seed)
    analyst = make_client("a_analyst")
    other = make_client("b_analyst")
    received_a: list[str] = []
    received_b: list[str] = []
    ready_a, ready_b = threading.Event(), threading.Event()
    url = f"{server}/api/v1/stream/alerts"
    reader_a = threading.Thread(
        target=_read_events, args=(url, _cookie_header(analyst), received_a, ready_a), daemon=True
    )
    reader_b = threading.Thread(
        target=_read_events, args=(url, _cookie_header(other), received_b, ready_b), daemon=True
    )
    reader_a.start()
    reader_b.start()
    assert ready_a.wait(5) and ready_b.wait(5)
    time.sleep(0.3)  # allow the subscriptions to register
    reply = analyst.patch(f"/api/v1/alerts/{ids['A']['alert']}", json={"status": "in_review"})
    assert reply.status_code == 200
    reader_a.join(timeout=5)
    assert any(line.startswith("data:") and "alert.updated" in line for line in received_a)
    assert not any(line.startswith("data:") for line in received_b)


def test_sse_rejects_missing_credentials(server: str) -> None:
    assert httpx.get(f"{server}/api/v1/stream/alerts", timeout=5).status_code == 401


# --- Valkey ------------------------------------------------------------------------------------


@pytest.fixture()
def valkey_client() -> Any:
    import valkey

    client = valkey.Valkey.from_url(VALKEY_URL)
    try:
        client.ping()
    except Exception:
        pytest.skip("Valkey is not reachable (valkey-server --port 6390)")
    return client


def test_valkey_rate_limit_backend(valkey_client: Any) -> None:
    from atlas.api.errors import ApiError
    from atlas.api.ratelimit import RateLimiter, ValkeyBackend

    limiter = RateLimiter(ValkeyBackend(valkey_client))
    limiter.reset()
    for _ in range(3):
        limiter.check("test:valkey", (3, 30))
    with pytest.raises(ApiError) as error:
        limiter.check("test:valkey", (3, 30))
    assert error.value.status == 429 and int(error.value.headers["Retry-After"]) <= 30
    limiter.reset()


def test_valkey_bus_isolates_tenants(valkey_client: Any) -> None:
    from atlas.api.events import ValkeyBus

    bus = ValkeyBus(VALKEY_URL)
    got: dict[str, list[str]] = {"a": [], "b": []}

    async def listen(name: str, tenant: str) -> None:
        stream = bus.subscribe(tenant)
        try:
            got[name].append(await asyncio.wait_for(stream.__anext__(), 1.0))
        except (TimeoutError, StopAsyncIteration):
            pass
        finally:
            await stream.aclose()

    async def run() -> None:
        tasks = [
            asyncio.ensure_future(listen("a", "tenant-a")),
            asyncio.ensure_future(listen("b", "tenant-b")),
        ]
        await asyncio.sleep(0.3)
        bus.publish("tenant-a", {"type": "alert.updated"})
        await asyncio.gather(*tasks)

    asyncio.run(run())
    assert got["a"] and "alert.updated" in got["a"][0] and got["b"] == []


def test_app_runs_with_valkey_backends(db_url: str, valkey_client: Any) -> None:
    from atlas.api.app import create_app
    from atlas.api.settings import Settings
    from conftest import _app_url
    from fastapi.testclient import TestClient

    settings = Settings(database_url=_app_url(db_url), valkey_url=VALKEY_URL, limit_ip=(3, 60))
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        codes = [client.get("/api/v1/watchlists/sources").status_code for _ in range(6)]
        assert 429 in codes
    ValkeyBackendReset = valkey_client
    for name in ValkeyBackendReset.scan_iter("rl:*"):
        ValkeyBackendReset.delete(name)
