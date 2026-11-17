"""Administration: users, API keys, audit log, risk configuration, billing, tenants, contact."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from atlas.api.audit import write_audit
from atlas.api.db import PLATFORM as PLATFORM_SCOPE
from atlas.api.deps import Authorized, Ctx, app_state, client_ip, request_id
from atlas.api.errors import PROBLEM_RESPONSES, bad_request, conflict, forbidden, not_found
from atlas.api.pagination import clamp_limit, decode_cursor, encode_cursor
from atlas.api.rbac import ADMIN, AUDIT, KEY_SCOPES, PLATFORM, TENANT_ROLES
from atlas.api.schemas import (
    ApiKeyCreate,
    DemoRequest,
    RiskConfig,
    TenantCreate,
    UserCreate,
    UserPatch,
)
from atlas.api.screening import DEFAULT_CONFIG
from atlas.api.security import hash_token, new_token

admin = APIRouter(prefix="/admin", tags=["admin"], responses=PROBLEM_RESPONSES)
billing = APIRouter(prefix="/billing", tags=["billing"], responses=PROBLEM_RESPONSES)
tenants = APIRouter(prefix="/tenants", tags=["tenants"], responses=PROBLEM_RESPONSES)
users = APIRouter(prefix="/users", tags=["users"], responses=PROBLEM_RESPONSES)
contact = APIRouter(prefix="/contact", tags=["contact"], responses=PROBLEM_RESPONSES)
DEMO = {"demo_data": True, "label": "Demo data: synthetic subscriptions and invoices"}


# --- Users -------------------------------------------------------------------------------------


@admin.get("/users", summary="List users of the tenant")
def list_users(
    cursor: str | None = None, limit: int = 50, ctx: Ctx = Authorized(ADMIN)
) -> dict[str, Any]:
    limit = clamp_limit(limit)
    after = decode_cursor(cursor, 2)
    sql = (
        "SELECT id, tenant_id, email, full_name, role, is_active, last_login_at, created_at "
        "FROM users WHERE true"
    )
    params: list[Any] = []
    if after:
        sql += " AND (created_at, id) > (%s::timestamptz, %s::uuid)"
        params += after
    sql += " ORDER BY created_at, id LIMIT %s"
    rows = ctx.conn.execute(sql, [*params, limit + 1]).fetchall()
    page = rows[:limit]
    nxt = (
        encode_cursor([page[-1]["created_at"].isoformat(), str(page[-1]["id"])])
        if len(rows) > limit
        else None
    )
    return {"items": page, "next_cursor": nxt}


def _assert_role_allowed(ctx: Ctx, role: str) -> None:
    if ctx.principal.role != "platform_admin" and role not in TENANT_ROLES:
        raise forbidden("This role can only be assigned by a platform administrator.")


@admin.post("/users", status_code=201, summary="Invite a user")
def create_user(body: UserCreate, request: Request, ctx: Ctx = Authorized(ADMIN)) -> dict[str, Any]:
    _assert_role_allowed(ctx, body.role)
    tenant_id = ctx.scope.tenant_id or body.tenant_id
    if body.role in TENANT_ROLES and tenant_id is None:
        raise bad_request("A tenant is required for tenant roles.", "tenant-required")
    if body.role not in TENANT_ROLES:
        tenant_id = None
    email = body.email.lower()
    if ctx.conn.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone():
        raise conflict("A user with this email already exists.", "email-taken")
    # The invited user sets a password through the reset flow; the placeholder cannot be used.
    placeholder = ctx.state.hasher.hash(new_token())
    try:
        with ctx.conn.transaction():
            row = ctx.conn.execute(
                "INSERT INTO users (tenant_id, email, full_name, password_hash, role) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (tenant_id, email, body.full_name, placeholder, body.role),
            ).fetchone()
    except UniqueViolation:
        raise conflict("A user with this email already exists.", "email-taken") from None
    assert row is not None
    token = new_token()
    ctx.conn.execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) "
        "VALUES (%s, %s, now() + interval '3 days')",
        (row["id"], hash_token(token, ctx.state.settings.token_pepper)),
    )
    ctx.state.mailer.send(
        email,
        "You have been invited",
        f"Set your password: {ctx.state.settings.web_origin}/reset-password?token={token}",
    )
    ctx.audit("user.created", str(row["id"]), {"role": body.role})
    return {"id": row["id"], "email": email, "role": body.role, "tenant_id": tenant_id}


@admin.patch("/users/{user_id}", summary="Change role or activation")
def patch_user(user_id: uuid.UUID, body: UserPatch, ctx: Ctx = Authorized(ADMIN)) -> dict[str, Any]:
    target = ctx.conn.execute("SELECT id, role FROM users WHERE id = %s", (user_id,)).fetchone()
    if target is None:
        raise not_found("User")
    if body.role is not None:
        _assert_role_allowed(ctx, body.role)
        if target["role"] not in TENANT_ROLES and ctx.principal.role != "platform_admin":
            raise forbidden()
    if user_id == ctx.principal.user_id and (body.is_active is False or body.role):
        raise bad_request("You cannot change your own role or deactivate yourself.", "self-change")
    if body.role is not None:
        ctx.conn.execute("UPDATE users SET role = %s WHERE id = %s", (body.role, user_id))
    if body.full_name is not None:
        ctx.conn.execute("UPDATE users SET full_name = %s WHERE id = %s", (body.full_name, user_id))
    if body.is_active is not None:
        ctx.conn.execute("UPDATE users SET is_active = %s WHERE id = %s", (body.is_active, user_id))
        if not body.is_active:
            ctx.conn.execute(
                "UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = %s "
                "AND revoked_at IS NULL",
                (user_id,),
            )
    ctx.audit("user.updated", str(user_id), body.model_dump(exclude_unset=True, mode="json"))
    return (
        ctx.conn.execute(
            "SELECT id, email, role, is_active, tenant_id FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        or {}
    )


@users.get("/me", summary="Profile of the signed-in user")
def profile(ctx: Ctx = Authorized()) -> dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT id, email, full_name, role, tenant_id, last_login_at FROM users WHERE id = %s",
        (ctx.principal.user_id,),
    ).fetchone()
    return row or {"email": ctx.principal.email, "role": ctx.principal.role}


# --- API keys ----------------------------------------------------------------------------------


@admin.get("/api-keys", summary="List API keys")
def list_keys(ctx: Ctx = Authorized(ADMIN)) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT id, name, prefix, scopes, expires_at, last_used_at, revoked_at, created_at "
        "FROM api_keys ORDER BY created_at DESC"
    ).fetchall()
    return {"items": rows}


@admin.post("/api-keys", status_code=201, summary="Create an API key (shown once)")
def create_key(body: ApiKeyCreate, ctx: Ctx = Authorized(ADMIN)) -> dict[str, Any]:
    scopes = sorted(set(body.scopes))
    if not set(scopes) <= (ctx.principal.permissions & KEY_SCOPES):
        raise forbidden("A key cannot carry permissions that the creator does not hold.")
    prefix = "atl_" + uuid.uuid4().hex[:8]
    secret = new_token(32)
    row = ctx.conn.execute(
        "INSERT INTO api_keys (tenant_id, name, prefix, secret_hash, scopes, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, now() + make_interval(days => %s)) RETURNING id, expires_at",
        (
            ctx.tenant_id,
            body.name,
            prefix,
            hash_token(secret, ctx.state.settings.token_pepper),
            scopes,
            body.expires_in_days or 90,
        ),
    ).fetchone()
    assert row is not None
    ctx.audit("api_key.created", str(row["id"]), {"key_prefix": prefix, "scopes": scopes})
    return {
        "id": row["id"],
        "name": body.name,
        "scopes": scopes,
        "expires_at": row["expires_at"],
        "api_key": f"{prefix}.{secret}",
        "notice": "Store this key now. It cannot be shown again.",
    }


@admin.delete("/api-keys/{key_id}", status_code=204, summary="Revoke an API key")
def revoke_key(key_id: uuid.UUID, ctx: Ctx = Authorized(ADMIN)) -> None:
    row = ctx.conn.execute(
        "UPDATE api_keys SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL "
        "RETURNING prefix",
        (key_id,),
    ).fetchone()
    if row is None:
        raise not_found("API key")
    ctx.audit("api_key.revoked", str(key_id), {"key_prefix": row["prefix"]})


# --- Audit -------------------------------------------------------------------------------------


@admin.get("/audit", summary="Audit log (newest first)")
def audit_log(
    action: str | None = Query(default=None, max_length=80),
    cursor: str | None = None,
    limit: int = 50,
    ctx: Ctx = Authorized(AUDIT),
) -> dict[str, Any]:
    limit = clamp_limit(limit)
    after = decode_cursor(cursor, 1)
    sql = (
        "SELECT id, tenant_id, actor_id, action, target, detail, request_id, created_at, "
        "row_hash FROM audit_log WHERE true"
    )
    params: list[Any] = []
    if action:
        sql += " AND action = %s"
        params.append(action)
    if after:
        sql += " AND id < %s"
        params.append(int(after[0]))
    sql += " ORDER BY id DESC LIMIT %s"
    rows = ctx.conn.execute(sql, [*params, limit + 1]).fetchall()
    page = rows[:limit]
    return {
        "items": page,
        "next_cursor": encode_cursor([page[-1]["id"]]) if len(rows) > limit else None,
    }


@admin.get("/audit/verify", summary="Verify the audit hash chain")
def audit_verify(ctx: Ctx = Authorized(PLATFORM)) -> dict[str, Any]:
    row = ctx.conn.execute("SELECT audit_verify() AS first_bad_id").fetchone()
    first_bad = row["first_bad_id"] if row else None
    return {"intact": first_bad is None, "first_invalid_id": first_bad}


# --- Risk configuration ------------------------------------------------------------------------


@admin.get("/risk-config", summary="Current risk configuration")
def get_risk_config(ctx: Ctx = Authorized(ADMIN)) -> dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT config, version, updated_at FROM risk_config WHERE tenant_id = %s", (ctx.tenant_id,)
    ).fetchone()
    if row is None:
        return {"config": DEFAULT_CONFIG, "version": 0, "updated_at": None, "is_default": True}
    return {**row, "config": {**DEFAULT_CONFIG, **row["config"]}, "is_default": False}


@admin.put("/risk-config", summary="Replace the risk configuration")
def put_risk_config(body: RiskConfig, ctx: Ctx = Authorized(ADMIN)) -> dict[str, Any]:
    config = body.model_dump(mode="json")
    row = ctx.conn.execute(
        "INSERT INTO risk_config (tenant_id, config, updated_by) VALUES (%s, %s, %s) "
        "ON CONFLICT (tenant_id) DO UPDATE SET config = EXCLUDED.config, "
        "version = risk_config.version + 1, updated_by = EXCLUDED.updated_by, updated_at = now() "
        "RETURNING version, updated_at",
        (ctx.tenant_id, Jsonb(config), ctx.principal.user_id),
    ).fetchone()
    assert row is not None
    ctx.audit("risk_config.updated", None, {"version": row["version"], "config": config})
    return {"config": config, **row}


# --- Billing -----------------------------------------------------------------------------------


@billing.get("/plan", summary="Current plan")
def current_plan(ctx: Ctx = Authorized(ADMIN)) -> dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT p.code, p.name, p.monthly_price, p.included_screenings, s.billing_cycle, s.price, "
        "s.start_date FROM subscriptions s JOIN plans p ON p.id = s.plan_id "
        "WHERE s.tenant_id = %s AND s.start_date <= current_date "
        "AND (s.end_date IS NULL OR s.end_date > current_date) ORDER BY s.start_date DESC LIMIT 1",
        (ctx.tenant_id,),
    ).fetchone()
    used = ctx.conn.execute(
        "SELECT coalesce(sum(screenings + batch_rows), 0) AS n FROM usage_daily "
        "WHERE tenant_id = %s AND day >= date_trunc('month', current_date)",
        (ctx.tenant_id,),
    ).fetchone()
    return {**DEMO, "plan": row, "screenings_this_month": used["n"] if used else 0}


@billing.get("/invoices", summary="Invoices")
def invoices(ctx: Ctx = Authorized(ADMIN)) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT id, period, status, due_date, total FROM invoices WHERE tenant_id = %s "
        "ORDER BY period DESC LIMIT 36",
        (ctx.tenant_id,),
    ).fetchall()
    return {**DEMO, "items": rows}


# --- Tenants -----------------------------------------------------------------------------------


@tenants.get("/current", summary="The caller's tenant")
def current_tenant(ctx: Ctx = Authorized()) -> dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT id, name, industry, region, country, is_demo, created_at FROM tenants "
        "WHERE id = %s",
        (ctx.principal.tenant_id or ctx.scope.tenant_id,),
    ).fetchone()
    if row is None:
        raise not_found("Tenant")
    return row


@tenants.get("", summary="All tenants (platform administrators)")
def list_tenants(ctx: Ctx = Authorized(PLATFORM)) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT id, name, industry, region, country, is_demo, created_at, churned_at "
        "FROM tenants ORDER BY created_at DESC LIMIT 500"
    ).fetchall()
    return {"items": rows}


@tenants.post("", status_code=201, summary="Create a tenant")
def create_tenant(body: TenantCreate, ctx: Ctx = Authorized(PLATFORM)) -> dict[str, Any]:
    row = ctx.conn.execute(
        "INSERT INTO tenants (name, industry, region, country) VALUES (%s, %s, %s, %s) "
        "RETURNING id, name, created_at",
        (body.name, body.industry, body.region, body.country),
    ).fetchone()
    assert row is not None
    ctx.audit("tenant.created", str(row["id"]))
    return row


# --- Contact (public) --------------------------------------------------------------------------


def _public(request: Request) -> Any:
    return app_state(request)


@contact.post("/demo-request", status_code=202, summary="Request a product demonstration")
def demo_request(
    body: DemoRequest, request: Request, state: Any = Depends(_public)
) -> dict[str, str]:
    state.limiter.check(f"contact:{client_ip(request)}", state.settings.limit_public_form)
    if body.website:  # honeypot field: accepted silently, not stored
        return {"status": "Thank you. We will be in touch."}
    import hashlib

    with state.db.session(PLATFORM_SCOPE) as conn:
        conn.execute(
            "INSERT INTO contact_requests (name, email, company, message, ip_hash) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                body.name,
                body.email.lower(),
                body.company,
                body.message,
                hashlib.sha256(client_ip(request).encode()).hexdigest()[:32],
            ),
        )
        write_audit(
            conn,
            "contact.demo_request",
            tenant_id=None,
            actor_id=None,
            request_id=request_id(request),
        )
    return {"status": "Thank you. We will be in touch."}
