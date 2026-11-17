"""Authentication: login, MFA, refresh rotation with reuse detection, sessions, password reset."""

from __future__ import annotations

import hashlib
import io
import uuid
from typing import Any

import psycopg
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from atlas.api.audit import write_audit
from atlas.api.db import PLATFORM, Row
from atlas.api.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    Authorized,
    Ctx,
    app_state,
    authenticate,
    client_ip,
    request_id,
    require_csrf,
)
from atlas.api.errors import PROBLEM_RESPONSES, ApiError, bad_request, problem_body, unauthorized
from atlas.api.rbac import MFA_REQUIRED_ROLES, ROLE_PERMISSIONS
from atlas.api.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    MfaSetupRequest,
    MfaVerifyRequest,
    ResetPasswordRequest,
)
from atlas.api.security import (
    PasswordPolicyError,
    check_password_policy,
    decode_jwt,
    hash_recovery,
    hash_token,
    issue_jwt,
    new_csrf,
    new_recovery_codes,
    new_token,
    new_totp_secret,
    totp_uri,
    verify_password,
    verify_totp,
)
from atlas.api.state import AppState

router = APIRouter(prefix="/auth", tags=["auth"], responses=PROBLEM_RESPONSES)
GENERIC_LOGIN_ERROR = "The email or password is not correct."


def _fail(state: AppState, request: Request, action: str, user: Row | None, why: str) -> ApiError:
    with state.db.session(PLATFORM) as conn:
        write_audit(
            conn,
            action,
            tenant_id=user["tenant_id"] if user else None,
            actor_id=user["id"] if user else None,
            detail={"reason": why, "ip": client_ip(request)},
            request_id=request_id(request),
        )
    return unauthorized(GENERIC_LOGIN_ERROR)


def _set_cookies(state: AppState, response: Response, access: str, refresh: str, csrf: str) -> None:
    s = state.settings
    common: dict[str, Any] = {"secure": s.cookie_secure, "samesite": "strict"}
    response.set_cookie(
        ACCESS_COOKIE, access, max_age=s.access_ttl_seconds, httponly=True, path="/", **common
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=s.idle_ttl_seconds,
        httponly=True,
        path="/api/v1/auth",
        **common,
    )
    response.set_cookie(
        CSRF_COOKIE, csrf, max_age=s.absolute_ttl_seconds, httponly=False, path="/", **common
    )


def _clear_cookies(response: Response) -> None:
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, "/api/v1/auth"), (CSRF_COOKIE, "/")):
        response.delete_cookie(name, path=path)


def _start_session(
    state: AppState,
    conn: psycopg.Connection[Row],
    request: Request,
    response: Response,
    user: Row,
    family_id: uuid.UUID | None = None,
    family_started: Any = None,
    csrf: str | None = None,
) -> dict[str, Any]:
    s = state.settings
    family = family_id or uuid.uuid4()
    refresh = new_token()
    conn.execute(
        "INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at, "
        "family_started_at, user_agent, ip) VALUES (%s, %s, %s, "
        "least(now() + make_interval(secs => %s), "
        "coalesce(%s, now()) + make_interval(secs => %s)), coalesce(%s, now()), %s, %s)",
        (
            user["id"],
            family,
            hash_token(refresh, s.token_pepper),
            s.idle_ttl_seconds,
            family_started,
            s.absolute_ttl_seconds,
            family_started,
            (request.headers.get("user-agent") or "")[:300],
            client_ip(request),
        ),
    )
    access = issue_jwt(
        s,
        str(user["id"]),
        "access",
        s.access_ttl_seconds,
        {"sid": str(family), "role": user["role"], "tid": str(user["tenant_id"] or "")},
    )
    _set_cookies(state, response, access, refresh, csrf or new_csrf())
    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "tenant_id": user["tenant_id"],
        "permissions": sorted(ROLE_PERMISSIONS[user["role"]]),
    }


def _mfa_secret(state: AppState, conn: psycopg.Connection[Row], user_id: uuid.UUID) -> Row | None:
    return conn.execute(
        "SELECT pgp_sym_decrypt(totp_secret_enc, %s) AS secret, enabled_at, recovery_hashes "
        "FROM user_mfa WHERE user_id = %s",
        (state.settings.data_key, user_id),
    ).fetchone()


@router.post("/login", summary="Sign in with email and password")
def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    state = app_state(request)
    s = state.settings
    email = body.email.lower()
    ip = client_ip(request)
    state.limiter.check(f"login-ip:{ip}", s.limit_login_ip)
    state.limiter.check(
        f"login-acct:{hashlib.sha256(email.encode()).hexdigest()}", s.limit_login_account
    )
    failure: ApiError | None = None
    result: dict[str, Any] | None = None
    with state.db.session(PLATFORM) as conn:
        user = conn.execute(
            "SELECT *, (locked_until IS NOT NULL AND locked_until > now()) AS locked "
            "FROM users WHERE email = %s",
            (email,),
        ).fetchone()
    if user is None or not user["is_active"]:
        verify_password(state.hasher, state.decoy_hash, body.password)
        failure = _fail(state, request, "auth.login_failed", None, "unknown_or_inactive")
    elif user["locked"]:
        failure = _fail(state, request, "auth.login_blocked", user, "locked")
    elif not verify_password(state.hasher, user["password_hash"], body.password):
        with state.db.session(PLATFORM) as conn:
            conn.execute(
                "UPDATE users SET failed_logins = failed_logins + 1, locked_until = CASE "
                "WHEN failed_logins + 1 >= %s THEN now() + make_interval(mins => "
                "least(60, power(2, failed_logins + 1 - %s)::int)) ELSE locked_until END "
                "WHERE id = %s",
                (s.lockout_threshold, s.lockout_threshold, user["id"]),
            )
        failure = _fail(state, request, "auth.login_failed", user, "bad_password")
    if failure is not None:
        raise failure
    assert user is not None
    with state.db.session(PLATFORM) as conn:
        conn.execute(
            "UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = %s", (user["id"],)
        )
        if state.hasher.check_needs_rehash(user["password_hash"]):
            conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (state.hasher.hash(body.password), user["id"]),
            )
        mfa = _mfa_secret(state, conn, user["id"])
        enrolled = bool(mfa and mfa["enabled_at"])
        if enrolled or user["role"] in MFA_REQUIRED_ROLES:
            typ = "mfa" if enrolled else "mfa_setup"
            token = issue_jwt(s, str(user["id"]), typ, s.mfa_token_ttl_seconds)
            result = {
                "status": "mfa_required" if enrolled else "mfa_setup_required",
                "mfa_token": token,
            }
        else:
            result = {"status": "ok", "user": _start_session(state, conn, request, response, user)}
            conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))
        write_audit(
            conn,
            "auth.login_password_ok",
            tenant_id=user["tenant_id"],
            actor_id=user["id"],
            detail={"next": result["status"], "ip": ip},
            request_id=request_id(request),
        )
    return result


def _user_for_mfa(
    state: AppState, conn: psycopg.Connection[Row], request: Request, token: str | None
) -> tuple[Row, str | None]:
    """Resolve the user from an MFA token or, failing that, an authenticated session."""
    if token:
        for typ in ("mfa", "mfa_setup"):
            payload = decode_jwt(state.settings, token, typ)
            if payload:
                user = conn.execute(
                    "SELECT * FROM users WHERE id = %s AND is_active", (uuid.UUID(payload["sub"]),)
                ).fetchone()
                if user:
                    return user, typ
        raise unauthorized("The MFA token is not valid or has expired.")
    principal = authenticate(request)
    if principal.user_id is None:
        raise unauthorized()
    user = conn.execute("SELECT * FROM users WHERE id = %s", (principal.user_id,)).fetchone()
    if user is None:
        raise unauthorized()
    return user, None


def _qr_svg(uri: str) -> str:
    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode()


@router.post("/mfa/setup", summary="Start TOTP enrollment")
def mfa_setup(body: MfaSetupRequest, request: Request) -> dict[str, Any]:
    state = app_state(request)
    with state.db.session(PLATFORM) as conn:
        user, _ = _user_for_mfa(state, conn, request, body.mfa_token)
        existing = _mfa_secret(state, conn, user["id"])
        if existing and existing["enabled_at"]:
            raise bad_request("MFA is already enabled for this account.", "mfa-already-enabled")
        secret = new_totp_secret()
        conn.execute(
            "INSERT INTO user_mfa (user_id, totp_secret_enc) VALUES (%s, pgp_sym_encrypt(%s, %s)) "
            "ON CONFLICT (user_id) DO UPDATE SET totp_secret_enc = EXCLUDED.totp_secret_enc, "
            "enabled_at = NULL, recovery_hashes = '{}'",
            (user["id"], secret, state.settings.data_key),
        )
        write_audit(
            conn,
            "auth.mfa_setup_started",
            tenant_id=user["tenant_id"],
            actor_id=user["id"],
            request_id=request_id(request),
        )
    uri = totp_uri(secret, user["email"])
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": _qr_svg(uri)}


@router.post("/mfa/verify", summary="Complete MFA enrollment or sign-in")
def mfa_verify(body: MfaVerifyRequest, request: Request, response: Response) -> dict[str, Any]:
    state = app_state(request)
    s = state.settings
    failure: ApiError | None = None
    result: dict[str, Any] = {}
    with state.db.session(PLATFORM) as conn:
        user, typ = _user_for_mfa(state, conn, request, body.mfa_token)
        state.limiter.check(f"mfa:{user['id']}", (10, 900))
        mfa = _mfa_secret(state, conn, user["id"])
        if mfa is None:
            raise bad_request("Start MFA enrollment first.", "mfa-not-started")
        enabling = not mfa["enabled_at"]
        ok = False
        if body.code and verify_totp(mfa["secret"], body.code):
            ok = True
        elif body.recovery_code and not enabling:
            digest = hash_recovery(body.recovery_code, s.token_pepper)
            if digest in mfa["recovery_hashes"]:
                conn.execute(
                    "UPDATE user_mfa SET recovery_hashes = array_remove(recovery_hashes, %s) "
                    "WHERE user_id = %s",
                    (digest, user["id"]),
                )
                ok = True
        if ok:
            if enabling:
                codes = new_recovery_codes()
                conn.execute(
                    "UPDATE user_mfa SET enabled_at = now(), recovery_hashes = %s "
                    "WHERE user_id = %s",
                    ([hash_recovery(c, s.token_pepper) for c in codes], user["id"]),
                )
                result["recovery_codes"] = codes
            if typ is not None:
                result["user"] = _start_session(state, conn, request, response, user)
                conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))
            result["status"] = "ok"
            write_audit(
                conn,
                "auth.mfa_verified",
                tenant_id=user["tenant_id"],
                actor_id=user["id"],
                detail={"enrolled": enabling},
                request_id=request_id(request),
            )
        else:
            failure = ApiError(401, "invalid-mfa-code", "Unauthorized", "The code is not correct.")
    if failure:
        with state.db.session(PLATFORM) as conn:
            write_audit(
                conn,
                "auth.mfa_failed",
                tenant_id=user["tenant_id"],
                actor_id=user["id"],
                request_id=request_id(request),
            )
        raise failure
    return result


@router.post("/refresh", summary="Rotate the refresh token")
def refresh(request: Request, response: Response) -> Any:
    state = app_state(request)
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise unauthorized("No refresh token.")
    require_csrf(request)
    digest = hash_token(token, state.settings.token_pepper)
    outcome = "ok"
    result: dict[str, Any] = {}
    user: Row | None = None
    with state.db.session(PLATFORM) as conn:
        row = conn.execute(
            "SELECT r.*, (r.expires_at < now()) AS expired, "
            "(r.family_started_at + make_interval(secs => %s) < now()) AS family_expired "
            "FROM refresh_tokens r WHERE r.token_hash = %s FOR UPDATE",
            (state.settings.absolute_ttl_seconds, digest),
        ).fetchone()
        if row is None:
            outcome = "unknown"
        else:
            user = conn.execute("SELECT * FROM users WHERE id = %s", (row["user_id"],)).fetchone()
            if row["used_at"] is not None:
                outcome = "reuse"
                conn.execute(
                    "UPDATE refresh_tokens SET revoked_at = now() WHERE family_id = %s "
                    "AND revoked_at IS NULL",
                    (row["family_id"],),
                )
                write_audit(
                    conn,
                    "auth.refresh_reuse_detected",
                    tenant_id=row_tenant(user),
                    actor_id=row["user_id"],
                    detail={"ip": client_ip(request)},
                    request_id=request_id(request),
                )
            elif row["revoked_at"] is not None or row["expired"] or row["family_expired"]:
                outcome = "expired"
            elif user is None or not user["is_active"]:
                outcome = "inactive"
            else:
                conn.execute(
                    "UPDATE refresh_tokens SET used_at = now(), last_seen_at = now() WHERE id = %s",
                    (row["id"],),
                )
                result = _start_session(
                    state,
                    conn,
                    request,
                    response,
                    user,
                    row["family_id"],
                    row["family_started_at"],
                    request.cookies.get(CSRF_COOKIE),
                )
    if outcome != "ok":
        error = unauthorized("The session has ended. Sign in again.")
        ended = JSONResponse(
            problem_body(error, request.url.path, request_id(request)),
            status_code=401,
            media_type="application/problem+json",
        )
        _clear_cookies(ended)
        return ended
    return {"status": "ok", "user": result}


def row_tenant(user: Row | None) -> uuid.UUID | None:
    return user["tenant_id"] if user else None


@router.post("/logout", status_code=204, summary="End the current session")
def logout(request: Request) -> Response:
    state = app_state(request)
    token = request.cookies.get(REFRESH_COOKIE)
    if request.cookies.get(ACCESS_COOKIE) or token:
        require_csrf(request)
    if token:
        with state.db.session(PLATFORM) as conn:
            row = conn.execute(
                "SELECT user_id, family_id FROM refresh_tokens WHERE token_hash = %s",
                (hash_token(token, state.settings.token_pepper),),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE refresh_tokens SET revoked_at = now() WHERE family_id = %s "
                    "AND revoked_at IS NULL",
                    (row["family_id"],),
                )
                write_audit(
                    conn,
                    "auth.logout",
                    tenant_id=None,
                    actor_id=row["user_id"],
                    request_id=request_id(request),
                )
    out = Response(status_code=204)
    _clear_cookies(out)
    return out


@router.post("/password/forgot", status_code=202, summary="Request a password reset link")
def forgot_password(body: ForgotPasswordRequest, request: Request) -> dict[str, str]:
    state = app_state(request)
    s = state.settings
    email = body.email.lower()
    state.limiter.check(f"reset-ip:{client_ip(request)}", s.limit_reset)
    state.limiter.check(f"reset-acct:{hashlib.sha256(email.encode()).hexdigest()}", s.limit_reset)
    with state.db.session(PLATFORM) as conn:
        user = conn.execute(
            "SELECT id, tenant_id FROM users WHERE email = %s AND is_active", (email,)
        ).fetchone()
        if user:
            token = new_token()
            conn.execute(
                "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) "
                "VALUES (%s, %s, now() + make_interval(secs => %s))",
                (user["id"], hash_token(token, s.token_pepper), s.reset_ttl_seconds),
            )
            write_audit(
                conn,
                "auth.password_reset_requested",
                tenant_id=user["tenant_id"],
                actor_id=user["id"],
                request_id=request_id(request),
            )
            state.mailer.send(
                email,
                "Reset your password",
                f"Use this link within one hour: {s.web_origin}/reset-password?token={token}\n"
                "If you did not request this, ignore this message.",
            )
    return {"status": "If the account exists, a reset message has been sent."}


@router.post("/password/reset", summary="Set a new password with a reset token")
def reset_password(body: ResetPasswordRequest, request: Request) -> dict[str, str]:
    state = app_state(request)
    digest = hash_token(body.token, state.settings.token_pepper)
    with state.db.session(PLATFORM) as conn:
        row = conn.execute(
            "SELECT t.id, t.user_id, u.email, u.tenant_id FROM password_reset_tokens t "
            "JOIN users u ON u.id = t.user_id WHERE t.token_hash = %s AND t.used_at IS NULL "
            "AND t.expires_at > now() AND u.is_active FOR UPDATE OF t",
            (digest,),
        ).fetchone()
        if row is None:
            raise bad_request("The reset link is not valid or has expired.", "invalid-reset-token")
        try:
            check_password_policy(body.new_password, [row["email"].split("@")[0], row["email"]])
        except PasswordPolicyError as error:
            raise bad_request(str(error), "weak-password") from None
        conn.execute("UPDATE password_reset_tokens SET used_at = now() WHERE id = %s", (row["id"],))
        conn.execute(
            "UPDATE users SET password_hash = %s, failed_logins = 0, locked_until = NULL "
            "WHERE id = %s",
            (state.hasher.hash(body.new_password), row["user_id"]),
        )
        conn.execute(
            "UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = %s "
            "AND revoked_at IS NULL",
            (row["user_id"],),
        )
        write_audit(
            conn,
            "auth.password_reset_completed",
            tenant_id=row["tenant_id"],
            actor_id=row["user_id"],
            request_id=request_id(request),
        )
    return {"status": "Password updated. Sign in with the new password."}


@router.get("/me", summary="Current principal")
def me(ctx: Ctx = Authorized()) -> dict[str, Any]:
    p = ctx.principal
    mfa = None
    if p.user_id:
        row = ctx.conn.execute(
            "SELECT enabled_at IS NOT NULL AS enabled FROM user_mfa WHERE user_id = %s",
            (p.user_id,),
        ).fetchone()
        mfa = bool(row and row["enabled"])
    return {
        "id": p.user_id,
        "kind": p.kind,
        "email": p.email,
        "role": p.role,
        "tenant_id": p.tenant_id,
        "permissions": sorted(p.permissions),
        "mfa_enabled": mfa,
    }


@router.get("/sessions", summary="Active sessions of the current user")
def sessions(ctx: Ctx = Authorized()) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT family_id AS id, family_started_at AS started_at, issued_at AS last_rotated_at, "
        "expires_at, user_agent, ip FROM refresh_tokens WHERE user_id = %s "
        "AND revoked_at IS NULL AND used_at IS NULL AND expires_at > now() "
        "ORDER BY family_started_at DESC",
        (ctx.principal.user_id,),
    ).fetchall()
    for row in rows:
        row["current"] = row["id"] == ctx.principal.session_id
    return {"items": rows}


@router.delete("/sessions/{session_id}", status_code=204, summary="Revoke one session")
def revoke_session(session_id: uuid.UUID, ctx: Ctx = Authorized()) -> Response:
    ctx.conn.execute(
        "UPDATE refresh_tokens SET revoked_at = now() WHERE family_id = %s AND user_id = %s "
        "AND revoked_at IS NULL",
        (session_id, ctx.principal.user_id),
    )
    ctx.audit("auth.session_revoked", str(session_id))
    return Response(status_code=204)


__all__ = ["Depends", "router"]
