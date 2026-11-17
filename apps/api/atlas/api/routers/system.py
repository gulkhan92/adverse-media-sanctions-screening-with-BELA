"""Health checks and the Server-Sent Events stream for live alert updates."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from atlas.api.deps import app_state, authenticate, scope_for
from atlas.api.errors import PROBLEM_RESPONSES, bad_request, forbidden
from atlas.api.rbac import VIEW

health = APIRouter(prefix="/health", tags=["health"])
stream = APIRouter(prefix="/stream", tags=["stream"], responses=PROBLEM_RESPONSES)
HEARTBEAT_SECONDS = 15


@health.get("/live", summary="Process liveness")
def live() -> dict[str, str]:
    return {"status": "ok"}


@health.get("/ready", summary="Readiness: database reachable")
def ready(request: Request) -> JSONResponse:
    ok = app_state(request).db.ready()
    return JSONResponse({"status": "ok" if ok else "unavailable"}, status_code=200 if ok else 503)


@stream.get("/alerts", summary="Live alert events for the caller's tenant (SSE)")
async def alert_stream(request: Request) -> StreamingResponse:
    # No database session is held for the life of the stream.
    state = app_state(request)
    principal = await asyncio.to_thread(authenticate, request)
    if VIEW not in principal.permissions:
        raise forbidden()
    scope = scope_for(principal, request)
    if scope.tenant_id is None:
        raise bad_request("Select a tenant with the X-Tenant-Id header.", "tenant-required")
    tenant = str(scope.tenant_id)

    async def events() -> AsyncIterator[str]:
        yield "retry: 5000\n: connected\n\n"
        source = state.bus.subscribe(tenant).__aiter__()
        pending: asyncio.Task[str] | None = None
        try:
            while not await request.is_disconnected():
                if pending is None:
                    pending = asyncio.ensure_future(source.__anext__())
                done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS)
                if not done:
                    yield ": heartbeat\n\n"
                    continue
                payload = pending.result()
                pending = None
                yield f"event: alert\ndata: {payload}\n\n"
        finally:
            if pending is not None:
                pending.cancel()
            await source.aclose()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
