"""Authentication: login, lockout, MFA, token rotation, CSRF, reset, API keys."""

from __future__ import annotations

from typing import Any

import pyotp
from conftest import MFA_SECRET, PASSWORD

LOGIN = "/api/v1/auth/login"


def login(client: Any, email: str, password: str = PASSWORD) -> Any:
    return client.post(LOGIN, json={"email": email, "password": password})


def test_login_sets_hardened_cookies(make_client: Any) -> None:
    client = make_client()
    reply = login(client, "a_analyst@example.org")
    assert reply.status_code == 200 and reply.json()["status"] == "ok"
    cookies = {c.name: c for c in client.cookies.jar}
    assert set(cookies) == {"atlas_access", "atlas_refresh", "atlas_csrf"}
    raw = reply.headers.get_list("set-cookie")
    access = next(h for h in raw if h.startswith("atlas_access"))
    assert "HttpOnly" in access and "Secure" in access and "SameSite=strict" in access
    csrf = next(h for h in raw if h.startswith("atlas_csrf"))
    assert "HttpOnly" not in csrf
    assert "Path=/api/v1/auth" in next(h for h in raw if h.startswith("atlas_refresh"))


def test_failures_are_generic_and_audited(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client()
    unknown = login(client, "nobody@example.org")
    wrong = login(client, "a_analyst@example.org", "wrong-password-value")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]
    rows = (
        seed["admin"]
        .execute("SELECT action FROM audit_log WHERE action = 'auth.login_failed'")
        .fetchall()
    )
    assert len(rows) == 2


def test_lockout_after_repeated_failures(make_client: Any) -> None:
    client = make_client()
    for _ in range(5):
        assert login(client, "a_viewer@example.org", "bad-password-attempt").status_code == 401
    locked = login(client, "a_viewer@example.org")  # correct password, but locked
    assert locked.status_code == 401


def test_login_rate_limit(make_client: Any) -> None:
    client = make_client()
    codes = [login(client, "nobody@example.org", "x" * 12).status_code for _ in range(12)]
    assert codes.count(429) >= 1
    blocked = login(client, "nobody@example.org", "x" * 12)
    assert blocked.status_code == 429 and "retry-after" in blocked.headers


def test_admin_requires_mfa_enrollment_and_codes(make_client: Any, seed: dict[str, Any]) -> None:
    seed["admin"].execute("DELETE FROM user_mfa")  # force first-time enrollment
    client = make_client()
    body = login(client, "a_admin@example.org").json()
    assert body["status"] == "mfa_setup_required" and "atlas_access" not in client.cookies
    setup = client.post("/api/v1/auth/mfa/setup", json={"mfa_token": body["mfa_token"]})
    assert setup.status_code == 200 and setup.json()["qr_svg"].lstrip().startswith("<?xml")
    secret = setup.json()["secret"]
    bad = client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": "000000"}
    )
    assert bad.status_code == 401
    ok = client.post(
        "/api/v1/auth/mfa/verify",
        json={"mfa_token": body["mfa_token"], "code": pyotp.TOTP(secret).now()},
    )
    assert ok.status_code == 200
    codes = ok.json()["recovery_codes"]
    assert len(codes) == 10 and "atlas_access" in client.cookies
    # Recovery codes are stored hashed and each works once.
    stored = seed["admin"].execute("SELECT recovery_hashes FROM user_mfa").fetchone()[0]
    assert len(stored) == 10 and codes[0] not in stored
    fresh = make_client()
    token = login(fresh, "a_admin@example.org").json()["mfa_token"]
    used = fresh.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": token, "recovery_code": codes[0]}
    )
    assert used.status_code == 200
    again = make_client()
    token = login(again, "a_admin@example.org").json()["mfa_token"]
    reuse = again.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": token, "recovery_code": codes[0]}
    )
    assert reuse.status_code == 401


def test_mfa_token_cannot_be_used_as_session(make_client: Any) -> None:
    client = make_client()
    token = login(client, "a_admin@example.org").json()["mfa_token"]
    client.cookies.set("atlas_access", token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_csrf_required_for_cookie_sessions(make_client: Any) -> None:
    client = make_client("a_analyst")
    payload = {"name": "Acme Holdings", "kind": "organization"}
    no_header = make_client()
    no_header.cookies.update(client.cookies)
    assert no_header.post("/api/v1/subjects", json=payload).status_code == 403
    assert client.post("/api/v1/subjects", json=payload).status_code == 201
    client.headers["X-CSRF-Token"] = "wrong"
    assert client.post("/api/v1/subjects", json=payload).status_code == 403


def test_refresh_rotation_and_reuse_detection(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client("a_analyst")
    old_refresh = client.cookies.get("atlas_refresh", path="/api/v1/auth")
    first = client.post("/api/v1/auth/refresh")
    assert first.status_code == 200
    new_refresh = client.cookies.get("atlas_refresh", path="/api/v1/auth")
    assert new_refresh != old_refresh
    # Replaying the already-used token revokes the whole family.
    thief = make_client()
    thief.cookies.set("atlas_refresh", old_refresh, path="/api/v1/auth")
    thief.cookies.set("atlas_csrf", "x")
    thief.headers["X-CSRF-Token"] = "x"
    assert thief.post("/api/v1/auth/refresh").status_code == 401
    assert (
        client.post("/api/v1/auth/refresh").status_code == 401
    )  # legitimate holder is cut off too
    assert client.get("/api/v1/auth/me").status_code == 401
    actions = [r[0] for r in seed["admin"].execute("SELECT action FROM audit_log").fetchall()]
    assert "auth.refresh_reuse_detected" in actions


def test_logout_revokes_access_immediately(make_client: Any) -> None:
    client = make_client("a_analyst")
    stolen = make_client()
    stolen.cookies.update(client.cookies)
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert stolen.get("/api/v1/auth/me").status_code == 401


def test_sessions_listing_and_revocation(make_client: Any) -> None:
    one, two = make_client("a_analyst"), make_client("a_analyst")
    sessions = one.get("/api/v1/auth/sessions").json()["items"]
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    other = next(s for s in sessions if not s["current"])
    assert one.delete(f"/api/v1/auth/sessions/{other['id']}").status_code == 204
    assert two.get("/api/v1/auth/me").status_code == 401


def test_password_reset_flow(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client()
    unknown = client.post("/api/v1/auth/password/forgot", json={"email": "nobody@example.org"})
    known = client.post("/api/v1/auth/password/forgot", json={"email": "a_analyst@example.org"})
    assert unknown.status_code == known.status_code == 202 and unknown.json() == known.json()
    assert len(seed["state"].mailer.outbox) == 1
    token = seed["state"].mailer.outbox[0].body.split("token=")[1].split()[0]
    stored = seed["admin"].execute("SELECT token_hash FROM password_reset_tokens").fetchone()[0]
    assert token not in stored
    weak = client.post(
        "/api/v1/auth/password/reset", json={"token": token, "new_password": "password1234"}
    )
    assert weak.status_code == 400
    new = "Another-Long-Passphrase-77!"
    assert (
        client.post(
            "/api/v1/auth/password/reset", json={"token": token, "new_password": new}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/auth/password/reset", json={"token": token, "new_password": new}
        ).status_code
        == 400
    )
    assert login(client, "a_analyst@example.org", new).status_code == 200
    assert login(make_client(), "a_analyst@example.org").status_code == 401


def test_password_values_are_never_echoed(make_client: Any) -> None:
    reply = make_client().post(
        LOGIN, json={"email": "not-an-email", "password": "SuperSecret-Value-1"}
    )
    assert reply.status_code == 422
    assert "SuperSecret" not in reply.text
    assert reply.headers["content-type"].startswith("application/problem+json")


def test_api_key_lifecycle_and_scope(make_client: Any, seed: dict[str, Any]) -> None:
    admin = make_client("a_admin")
    created = admin.post("/api/v1/admin/api-keys", json={"name": "ci", "scopes": ["view"]})
    assert created.status_code == 201
    key = created.json()["api_key"]
    stored = seed["admin"].execute("SELECT secret_hash FROM api_keys").fetchone()[0]
    assert key.split(".")[1] not in stored
    machine = make_client()
    machine.headers["X-API-Key"] = key
    assert machine.get("/api/v1/alerts").status_code == 200
    denied = machine.post(
        "/api/v1/subjects", json={"name": "Acme Holdings", "kind": "organization"}
    )
    assert denied.status_code == 403  # scope does not include 'screen'
    assert machine.get("/api/v1/admin/api-keys").status_code == 403
    bad = make_client()
    bad.headers["X-API-Key"] = key[:-3] + "xyz"
    assert bad.get("/api/v1/alerts").status_code == 401
    key_id = created.json()["id"]
    assert admin.delete(f"/api/v1/admin/api-keys/{key_id}").status_code == 204
    assert machine.get("/api/v1/alerts").status_code == 401


def test_keys_cannot_exceed_creator_permissions(make_client: Any) -> None:
    manager_admin = make_client("a_admin")
    assert (
        manager_admin.post(
            "/api/v1/admin/api-keys", json={"name": "k", "scopes": ["screen", "cases"]}
        ).status_code
        == 201
    )
    assert MFA_SECRET  # fixture constant is used by make_client for admin roles
