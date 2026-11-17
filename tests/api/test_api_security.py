"""Audit hash chain, append-only log, limits, headers, problem responses, and admin flows."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from api_data import populate
from conftest import _app_url


def test_audit_chain_is_intact_and_detects_tampering(
    make_client: Any, seed: dict[str, Any]
) -> None:
    client = make_client("a_analyst")
    client.post("/api/v1/subjects", json={"name": "Acme Corp", "kind": "organization"})
    platform = make_client("platform")
    assert platform.get("/api/v1/admin/audit/verify").json() == {
        "intact": True,
        "first_invalid_id": None,
    }
    rows = (
        seed["admin"]
        .execute("SELECT id, prev_hash, row_hash FROM audit_log ORDER BY id")
        .fetchall()
    )
    assert len(rows) >= 3 and rows[0][1] is None
    assert all(rows[i][1] == rows[i - 1][2] for i in range(1, len(rows)))
    # Tamper as the table owner, with the guard trigger disabled.
    admin = seed["admin"]
    admin.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_no_update")
    admin.execute("UPDATE audit_log SET action = 'forged' WHERE id = 2")
    admin.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_no_update")
    verdict = platform.get("/api/v1/admin/audit/verify").json()
    assert verdict == {"intact": False, "first_invalid_id": 2}


def test_audit_log_is_append_only(seed: dict[str, Any], db_url: str, make_client: Any) -> None:
    make_client("a_analyst")  # writes login audit rows
    with psycopg.connect(_app_url(db_url)) as app:
        app.execute("SELECT set_config('app.platform', 'on', true)")
        for sql in (
            "UPDATE audit_log SET action = 'x'",
            "DELETE FROM audit_log",
            "TRUNCATE audit_log",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(sql)
            app.rollback()
    # Even the owner cannot change rows while the guard trigger is active.
    with pytest.raises(psycopg.errors.RaiseException):
        seed["admin"].execute("DELETE FROM audit_log")


def test_audit_entries_carry_request_ids_and_no_secrets(
    make_client: Any, seed: dict[str, Any]
) -> None:
    client = make_client("a_admin")
    client.post(
        "/api/v1/admin/api-keys",
        json={"name": "k", "scopes": ["view"]},
        headers={"X-Request-ID": "req-abcdef-123456"},
    )
    row = (
        seed["admin"]
        .execute("SELECT request_id, detail::text FROM audit_log WHERE action = 'api_key.created'")
        .fetchone()
    )
    assert row[0] == "req-abcdef-123456"
    assert "atl_" in row[1] and "secret" not in row[1].lower()
    everything = " ".join(
        r[0] for r in seed["admin"].execute("SELECT detail::text FROM audit_log").fetchall()
    )
    assert "Correct-Horse" not in everything


def test_security_headers_and_request_id(make_client: Any) -> None:
    reply = make_client().get("/api/v1/health/live", headers={"X-Request-ID": "trace-0001-abcd"})
    h = reply.headers
    assert h["x-request-id"] == "trace-0001-abcd"
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["strict-transport-security"].startswith("max-age=")
    assert h["referrer-policy"] == "no-referrer" and "permissions-policy" in h
    assert h["cache-control"] == "no-store"
    assert (
        "access-control-allow-origin"
        not in make_client()
        .get("/api/v1/health/live", headers={"Origin": "https://evil.example"})
        .headers
    )
    bad_id = make_client().get("/api/v1/health/live", headers={"X-Request-ID": "bad id\t"})
    assert bad_id.headers["x-request-id"] != "bad id\t"


def test_problem_details_for_errors(make_client: Any) -> None:
    client = make_client("a_viewer")
    missing = client.get("/api/v1/nope")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    body = missing.json()
    assert {"type", "title", "status", "instance", "request_id"} <= set(body)
    assert client.get("/api/v1/alerts/not-a-uuid").status_code == 422
    assert make_client().get("/api/v1/alerts").json()["status"] == 401


def test_payload_size_limit(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client("a_analyst")
    huge = {"name": "x" * 2_000_000, "kind": "person"}
    reply = client.post("/api/v1/subjects", json=huge)
    assert reply.status_code == 413 and reply.json()["type"].endswith("payload-too-large")


def test_ip_and_user_rate_limits(make_client: Any, seed: dict[str, Any]) -> None:
    settings = seed["state"].settings
    original = (settings.limit_ip, settings.limit_user)
    settings.limit_user = (5, 60)
    try:
        client = make_client("a_viewer")
        codes = [client.get("/api/v1/alerts").status_code for _ in range(8)]
        assert codes[:3] == [200, 200, 200] and 429 in codes
        limited = next(
            r for r in (client.get("/api/v1/alerts") for _ in range(2)) if r.status_code == 429
        )
        assert int(limited.headers["retry-after"]) >= 1
        seed["state"].limiter.reset()
        settings.limit_ip = (3, 60)
        anonymous = make_client()
        codes = [anonymous.get("/api/v1/watchlists/sources").status_code for _ in range(6)]
        assert 429 in codes
    finally:
        settings.limit_ip, settings.limit_user = original
        seed["state"].limiter.reset()


def test_api_key_rate_limit(make_client: Any, seed: dict[str, Any]) -> None:
    settings = seed["state"].settings
    original = settings.limit_api_key
    settings.limit_api_key = (3, 60)
    try:
        key = (
            make_client("a_admin")
            .post("/api/v1/admin/api-keys", json={"name": "k", "scopes": ["view"]})
            .json()["api_key"]
        )
        machine = make_client()
        machine.headers["X-API-Key"] = key
        assert 429 in [machine.get("/api/v1/alerts").status_code for _ in range(6)]
    finally:
        settings.limit_api_key = original


def test_contact_form(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client()
    body = {"name": "Sam Doe", "email": "sam@example.org", "message": "We would like a demo."}
    assert client.post("/api/v1/contact/demo-request", json=body).status_code == 202
    spam = client.post("/api/v1/contact/demo-request", json={**body, "website": "http://spam"})
    assert spam.status_code == 202
    stored = seed["admin"].execute("SELECT count(*) FROM contact_requests").fetchone()[0]
    assert stored == 1  # the honeypot submission is dropped
    assert (
        client.post("/api/v1/contact/demo-request", json={**body, "email": "bad"}).status_code
        == 422
    )
    codes = [client.post("/api/v1/contact/demo-request", json=body).status_code for _ in range(8)]
    assert 429 in codes


def test_health_and_openapi(make_client: Any) -> None:
    client = make_client()
    assert client.get("/api/v1/health/live").json() == {"status": "ok"}
    assert client.get("/api/v1/health/ready").json() == {"status": "ok"}
    spec = client.get("/api/openapi.json").json()
    assert spec["info"]["title"] == "Atlas Screening API"
    assert "/api/v1/screenings/batch" in spec["paths"]
    assert "examples" in spec["components"]["schemas"]["SubjectIn"]
    assert client.get("/api/docs").status_code == 200


def test_invited_user_sets_password_and_signs_in(make_client: Any, seed: dict[str, Any]) -> None:
    admin = make_client("a_admin")
    created = admin.post(
        "/api/v1/admin/users", json={"email": "New.Person@Example.org", "role": "analyst"}
    )
    assert created.status_code == 201 and created.json()["email"] == "new.person@example.org"
    mail = seed["state"].mailer.outbox[-1]
    token = mail.body.split("token=")[1].strip()
    password = "Fresh-Secret-Phrase-2026"
    client = make_client()
    assert (
        client.post(
            "/api/v1/auth/password/reset", json={"token": token, "new_password": password}
        ).status_code
        == 200
    )
    reply = client.post(
        "/api/v1/auth/login", json={"email": "new.person@example.org", "password": password}
    )
    assert reply.json()["user"]["role"] == "analyst"
    dup = admin.post(
        "/api/v1/admin/users", json={"email": "new.person@example.org", "role": "viewer"}
    )
    assert dup.status_code == 409


def test_role_assignment_limits(make_client: Any, seed: dict[str, Any]) -> None:
    admin = make_client("a_admin")
    assert (
        admin.post(
            "/api/v1/admin/users", json={"email": "x@example.org", "role": "platform_admin"}
        ).status_code
        == 403
    )
    assert (
        admin.post(
            "/api/v1/admin/users", json={"email": "y@example.org", "role": "finance_analyst"}
        ).status_code
        == 403
    )
    own = seed["users"]["a_admin"]
    assert admin.patch(f"/api/v1/admin/users/{own}", json={"is_active": False}).status_code == 400


def test_deactivated_user_loses_access_immediately(make_client: Any, seed: dict[str, Any]) -> None:
    admin = make_client("a_admin")
    analyst = make_client("a_analyst")
    assert analyst.get("/api/v1/alerts").status_code == 200
    uid = seed["users"]["a_analyst"]
    assert admin.patch(f"/api/v1/admin/users/{uid}", json={"is_active": False}).status_code == 200
    assert analyst.get("/api/v1/alerts").status_code == 401
    assert (
        make_client()
        .post("/api/v1/auth/login", json={"email": "a_analyst@example.org", "password": "x" * 14})
        .status_code
        == 401
    )


def test_risk_config_changes_screening_levels(make_client: Any, seed: dict[str, Any]) -> None:
    populate(seed)
    admin = make_client("a_admin")
    default = admin.get("/api/v1/admin/risk-config").json()
    assert default["is_default"] and default["version"] == 0
    subject = {"subject": {"name": "Viktor Petrovich Example", "kind": "person"}}
    before = admin.post("/api/v1/screenings", json=subject).json()
    strict = {"thresholds": {"medium": 50.0, "high": 75.0, "critical": 95.0}}
    saved = admin.put("/api/v1/admin/risk-config", json=strict)
    assert saved.status_code == 200 and saved.json()["version"] == 1
    after = admin.post("/api/v1/screenings", json=subject).json()
    assert before["risk_level"] == "high" and after["risk_level"] == "medium"
    assert after["config_version"].endswith(".1") and before["config_version"].endswith(".0")
    bad = admin.put(
        "/api/v1/admin/risk-config",
        json={"thresholds": {"medium": 90.0, "high": 50.0, "critical": 60.0}},
    )
    assert bad.status_code == 422
    assert make_client("a_manager").put("/api/v1/admin/risk-config", json=strict).status_code == 403


def test_tenant_endpoints(make_client: Any, seed: dict[str, Any]) -> None:
    current = make_client("a_viewer").get("/api/v1/tenants/current").json()
    assert current["name"] == "Tenant A"
    platform = make_client("platform")
    created = platform.post("/api/v1/tenants", json={"name": "Tenant C", "country": "gb"})
    assert created.status_code == 201
