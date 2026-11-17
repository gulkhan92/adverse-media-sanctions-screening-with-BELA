"""Pure ASGI middleware: request IDs, per-IP rate limit, body size limit, security headers."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from atlas.api.errors import ApiError, problem_body, too_many

REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
DOC_PATHS = ("/api/docs", "/api/redoc", "/api/openapi.json")
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOC_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
)


class BodyTooLarge(Exception):
    pass


class SecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        inbound = headers.get("x-request-id", "")
        rid = inbound if REQUEST_ID.match(inbound) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid
        path: str = scope["path"]
        state = scope["app"].state.atlas
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                out = list(message.get("headers", []))
                for name, value in self.security_headers(path, state.settings.cookie_secure, rid):
                    out.append((name.encode(), value.encode()))
                message = {**message, "headers": out}
            await send(message)

        async def reject(error: ApiError) -> None:
            body = json.dumps(problem_body(error, path, rid)).encode()
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": error.status,
                    "headers": [
                        (b"content-type", b"application/problem+json"),
                        (b"content-length", str(len(body)).encode()),
                        *[(k.lower().encode(), v.encode()) for k, v in error.headers.items()],
                    ],
                }
            )
            await send_wrapper({"type": "http.response.body", "body": body})

        if path != "/api/v1/health/live":
            client = scope.get("client") or ("unknown", 0)
            ip = client[0]
            if state.settings.trust_proxy_headers and headers.get("x-forwarded-for"):
                ip = headers["x-forwarded-for"].split(",")[0].strip()
            try:
                state.limiter.check(f"ip:{ip}", state.settings.limit_ip)
            except ApiError as error:
                await reject(error)
                return

        limit = (
            state.settings.max_batch_body_bytes
            if path.endswith(("/screenings/batch", "/screenings/batch/upload"))
            else state.settings.max_body_bytes
        )
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await reject(
                ApiError(413, "payload-too-large", "Payload too large", f"Limit is {limit} bytes.")
            )
            return

        received = 0

        async def receive_wrapper() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge
            return message

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except BodyTooLarge:
            if not started:
                await reject(
                    ApiError(
                        413, "payload-too-large", "Payload too large", f"Limit is {limit} bytes."
                    )
                )

    @staticmethod
    def security_headers(path: str, secure: bool, rid: str) -> list[tuple[str, str]]:
        docs = path.startswith(DOC_PATHS)
        headers = [
            ("X-Request-ID", rid),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"),
            ("Cross-Origin-Resource-Policy", "same-origin"),
            ("Content-Security-Policy", DOC_CSP if docs else API_CSP),
        ]
        if not docs:
            headers.append(("Cache-Control", "no-store"))
        if secure:
            headers.append(("Strict-Transport-Security", "max-age=63072000; includeSubDomains"))
        return headers


__all__: list[Any] = ["SecurityMiddleware", "too_many"]
