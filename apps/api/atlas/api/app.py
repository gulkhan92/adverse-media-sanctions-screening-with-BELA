"""FastAPI application factory. Run with ``uvicorn atlas.api.app:app``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from atlas.api.db import Database
from atlas.api.errors import ApiError, problem_body
from atlas.api.events import MemoryBus, ValkeyBus
from atlas.api.mailer import Mailer
from atlas.api.middleware import SecurityMiddleware
from atlas.api.ratelimit import MemoryBackend, RateLimiter, ValkeyBackend
from atlas.api.routers import admin, alerts, analytics, auth, knowledge, screenings, system
from atlas.api.security import dummy_hash, make_hasher
from atlas.api.settings import Settings, finalize, from_env
from atlas.api.state import AppState

log = logging.getLogger("atlas.api")
PROBLEM = "application/problem+json"
DESCRIPTION = (
    "Adverse media and sanctions screening API. Results are leads for human review, not "
    "determinations."
)


def build_state(settings: Settings) -> AppState:
    backend: MemoryBackend | ValkeyBackend = MemoryBackend()
    bus: Any = MemoryBus()
    if settings.valkey_url:
        import valkey

        client = valkey.Valkey.from_url(settings.valkey_url)
        backend = ValkeyBackend(client)
        bus = ValkeyBus(settings.valkey_url)
    hasher = make_hasher(settings)
    extras: dict[str, Any] = {}
    if settings.vector_search:
        from atlas.api.embedding_client import make_nel_embedder

        extras["embedder"] = make_nel_embedder(settings.nel_url)
    return AppState(
        settings=settings,
        db=Database(settings),
        hasher=hasher,
        limiter=RateLimiter(backend),
        bus=bus,
        mailer=Mailer(settings),
        decoy_hash=dummy_hash(hasher),
        extras=extras,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    config = finalize(settings) if settings else from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = build_state(config)
        app.state.atlas = state
        yield
        state.db.close()

    app = FastAPI(
        title="Atlas Screening API",
        version="0.4.0",
        description=DESCRIPTION,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(SecurityMiddleware)

    @app.exception_handler(ApiError)
    async def api_error(request: Request, error: ApiError) -> JSONResponse:
        rid = str(getattr(request.state, "request_id", ""))
        return JSONResponse(
            problem_body(error, request.url.path, rid),
            status_code=error.status,
            headers=error.headers,
            media_type=PROBLEM,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError) -> JSONResponse:
        # Field names and messages only: submitted values (passwords) are never echoed.
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in error.errors()
        ]
        problem = ApiError(422, "validation-error", "Validation failed", extra={"errors": errors})
        rid = str(getattr(request.state, "request_id", ""))
        return JSONResponse(
            problem_body(problem, request.url.path, rid), status_code=422, media_type=PROBLEM
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
        slug = {404: "not-found", 405: "method-not-allowed"}.get(error.status_code, "http-error")
        problem = ApiError(error.status_code, slug, "Request failed", str(error.detail))
        rid = str(getattr(request.state, "request_id", ""))
        return JSONResponse(
            problem_body(problem, request.url.path, rid),
            status_code=error.status_code,
            media_type=PROBLEM,
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        rid = str(getattr(request.state, "request_id", ""))
        log.exception("unhandled error request_id=%s", rid)
        problem = ApiError(
            500, "internal-error", "Internal server error", "The request could not be completed."
        )
        return JSONResponse(
            problem_body(problem, request.url.path, rid), status_code=500, media_type=PROBLEM
        )

    prefix = "/api/v1"
    for router in (
        system.health,
        auth.router,
        admin.users,
        admin.tenants,
        screenings.screenings,
        screenings.subjects,
        alerts.alerts,
        alerts.cases,
        knowledge.articles,
        knowledge.entities,
        knowledge.graph,
        knowledge.watchlists,
        knowledge.ingestion,
        analytics.analytics,
        analytics.platform,
        admin.billing,
        admin.admin,
        admin.contact,
        system.stream,
    ):
        app.include_router(router, prefix=prefix)
    return app


def factory() -> FastAPI:
    """``uvicorn --factory atlas.api.app:factory``"""
    return create_app()
