"""Request dependencies: authentication, permission checks, CSRF, rate limits, tenant scope."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import psycopg
from fastapi import Depends, Request

from atlas.api.audit import write_audit
from atlas.api.db import PLATFORM, Row, Scope
from atlas.api.errors import ApiError, bad_request, forbidden, unauthorized
from atlas.api.rbac import KEY_SCOPES, PLATFORM_SCOPE_ROLES, ROLE_PERMISSIONS
from atlas.api.security import constant_equal, decode_jwt, hash_token
from atlas.api.state import AppState

ACCESS_COOKIE = "atlas_access"
REFRESH_COOKIE = "atlas_refresh"
CSRF_COOKIE = "atlas_csrf"
CSRF_HEADER = "x-csrf-token"
log = logging.getLogger("atlas.api")
API_KEY_HEADER = "x-api-key"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def app_state(request: Request) -> AppState:
    state: AppState = request.app.state.atlas
    return state


def client_ip(request: Request) -> str:
    state = app_state(request)
    if state.settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


@dataclass(frozen=True)
class Principal:
    kind: str  # user or api_key
    role: str
    permissions: frozenset[str]
    email: str
    user_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    session_id: uuid.UUID | None = None
    key_id: uuid.UUID | None = None


def require_csrf(request: Request) -> None:
    """Double-submit check for state-changing requests authenticated by cookie."""
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not constant_equal(cookie, header):
        raise forbidden("The CSRF token is missing or invalid.")


def _principal_from_key(state: AppState, header: str) -> Principal:
    prefix, _, secret = header.partition(".")
    if not prefix.startswith("atl_") or not secret:
        raise unauthorized("The API key is not valid.")
    with state.db.session(PLATFORM) as conn:
        row = conn.execute(
            "SELECT k.id, k.tenant_id, k.secret_hash, k.scopes, k.expires_at, k.revoked_at, "
            "(k.expires_at IS NOT NULL AND k.expires_at < now()) AS expired, t.churned_at "
            "FROM api_keys k JOIN tenants t ON t.id = k.tenant_id "
            "WHERE k.prefix = %s",
            (prefix,),
        ).fetchone()
        expected = hash_token(secret, state.settings.token_pepper)
        if row is None or not constant_equal(row["secret_hash"], expected):
            raise unauthorized("The API key is not valid.")
        if row["revoked_at"] is not None or row["churned_at"] is not None:
            raise unauthorized("The API key is not valid.")
        if row["expired"]:
            raise unauthorized("The API key has expired.")
        conn.execute(
            "UPDATE api_keys SET last_used_at = now() WHERE id = %s "
            "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')",
            (row["id"],),
        )
    permissions = frozenset(row["scopes"]) & KEY_SCOPES
    return Principal(
        kind="api_key",
        role="api_key",
        permissions=permissions,
        email=f"api-key:{prefix}",
        user_id=None,
        tenant_id=row["tenant_id"],
        key_id=row["id"],
    )


def _principal_from_cookie(state: AppState, request: Request, token: str) -> Principal:
    payload = decode_jwt(state.settings, token, "access")
    if payload is None:
        raise unauthorized("The session is not valid.")
    try:
        user_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except (KeyError, ValueError):
        raise unauthorized("The session is not valid.") from None
    with state.db.session(PLATFORM) as conn:
        row = conn.execute(
            "SELECT u.id, u.tenant_id, u.role, u.email, u.is_active, "
            "EXISTS (SELECT 1 FROM refresh_tokens r WHERE r.family_id = %s "
            "AND r.user_id = u.id AND r.revoked_at IS NULL) AS session_ok "
            "FROM users u WHERE u.id = %s",
            (session_id, user_id),
        ).fetchone()
    if row is None or not row["is_active"] or not row["session_ok"]:
        raise unauthorized("The session is not valid.")
    return Principal(
        kind="user",
        role=row["role"],
        permissions=ROLE_PERMISSIONS[row["role"]],
        email=row["email"],
        user_id=row["id"],
        tenant_id=row["tenant_id"],
        session_id=session_id,
    )


def authenticate(request: Request) -> Principal:
    state = app_state(request)
    api_key = request.headers.get(API_KEY_HEADER)
    if api_key:
        principal = _principal_from_key(state, api_key)
        state.limiter.check(f"key:{principal.key_id}", state.settings.limit_api_key)
        return principal
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise unauthorized()
    principal = _principal_from_cookie(state, request, token)
    require_csrf(request)
    state.limiter.check(f"user:{principal.user_id}", state.settings.limit_user)
    return principal


def scope_for(principal: Principal, request: Request) -> Scope:
    header = request.headers.get("x-tenant-id")
    if principal.role == "platform_admin" and header:
        try:
            return Scope(tenant_id=uuid.UUID(header))
        except ValueError:
            raise bad_request("X-Tenant-Id must be a UUID.") from None
    if principal.role in PLATFORM_SCOPE_ROLES:
        return PLATFORM
    if principal.tenant_id is None:
        raise forbidden("The account is not assigned to a tenant.")
    return Scope(tenant_id=principal.tenant_id)


@dataclass
class Ctx:
    """Per-request context: authenticated principal and an RLS-scoped transaction."""

    request: Request
    state: AppState
    principal: Principal
    scope: Scope
    conn: psycopg.Connection[Row]
    callbacks: list[Callable[[], None]] = field(default_factory=list)

    def after_commit(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` once the request transaction has committed (not on failure)."""
        self.callbacks.append(fn)

    @property
    def tenant_id(self) -> uuid.UUID:
        if self.scope.tenant_id is None:
            raise bad_request("Select a tenant with the X-Tenant-Id header.", "tenant-required")
        return self.scope.tenant_id

    def audit(
        self, action: str, target: str | None = None, detail: dict[str, Any] | None = None
    ) -> None:
        write_audit(
            self.conn,
            action,
            tenant_id=self.scope.tenant_id or self.principal.tenant_id,
            actor_id=self.principal.user_id,
            target=target,
            detail={**(detail or {}), "actor": self.principal.email},
            request_id=request_id(self.request),
        )

    def publish(self, event: dict[str, Any]) -> None:
        self.state.bus.publish(str(self.tenant_id), event)


def audit_denied(state: AppState, request: Request, principal: Principal, permission: str) -> None:
    with state.db.session(PLATFORM) as conn:
        write_audit(
            conn,
            "authz.denied",
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            target=request.url.path,
            detail={"permission": permission, "method": request.method, "actor": principal.email},
            request_id=request_id(request),
        )


def context(permission: str | None = None) -> Callable[..., Iterator[Ctx]]:
    """Dependency factory: authenticate, check ``permission``, and open a scoped transaction."""

    def dependency(request: Request) -> Iterator[Ctx]:
        state = app_state(request)
        principal = authenticate(request)
        if permission and permission not in principal.permissions:
            audit_denied(state, request, principal, permission)
            raise forbidden()
        scope = scope_for(principal, request)
        with state.db.session(scope) as conn:
            ctx = Ctx(request, state, principal, scope, conn)
            yield ctx
        for callback in ctx.callbacks:  # the transaction has committed
            try:
                callback()
            except Exception:  # noqa: BLE001 - a follow-up job must not fail the request
                log.exception("post-commit callback failed")

    return dependency


def Authorized(permission: str | None = None) -> Any:  # noqa: N802
    return Depends(context(permission))


def public_session(request: Request) -> Iterator[tuple[AppState, psycopg.Connection[Row]]]:
    """Platform-scoped session for pre-authentication endpoints (login, reset, contact)."""
    state = app_state(request)
    with state.db.session(PLATFORM) as conn:
        yield state, conn


__all__ = [
    "ACCESS_COOKIE",
    "CSRF_COOKIE",
    "REFRESH_COOKIE",
    "ApiError",
    "Authorized",
    "Ctx",
    "Principal",
    "app_state",
    "authenticate",
    "client_ip",
    "context",
    "public_session",
    "request_id",
    "require_csrf",
]
