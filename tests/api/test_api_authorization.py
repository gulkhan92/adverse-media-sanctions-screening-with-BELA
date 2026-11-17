"""Role matrix for every endpoint group and cross-tenant isolation."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from api_data import populate
from conftest import _app_url

ROLES = ["a_viewer", "a_analyst", "a_manager", "a_admin", "finance", "platform"]
# (permission group, method, path, json body, roles allowed)
VIEW = {"a_viewer", "a_analyst", "a_manager", "a_admin", "platform"}
SCREEN = {"a_analyst", "a_manager", "a_admin", "platform"}
OPS = {"a_manager", "a_admin", "platform"}
ADMIN = {"a_admin", "platform"}
PLATFORM = {"platform"}
REVENUE = {"finance", "platform"}


def endpoints(ids: dict[str, Any]) -> list[tuple[str, str, str, Any, set[str]]]:
    a = ids["A"]
    return [
        ("view", "GET", "/api/v1/alerts", None, VIEW),
        ("view", "GET", f"/api/v1/alerts/{a['alert']}", None, VIEW),
        ("view", "GET", "/api/v1/subjects", None, VIEW),
        ("view", "GET", "/api/v1/entities/search?q=alpha", None, VIEW),
        ("view", "GET", f"/api/v1/graph/neighbors?entity_id={ids['entities'][0]}", None, VIEW),
        ("view", "GET", "/api/v1/watchlists/sources", None, VIEW),
        ("view", "GET", "/api/v1/articles", None, VIEW),
        ("view", "GET", "/api/v1/analytics/overview", None, VIEW),
        (
            "screen",
            "POST",
            "/api/v1/subjects",
            {"name": "Acme Corp", "kind": "organization"},
            SCREEN,
        ),
        (
            "screen",
            "POST",
            "/api/v1/screenings",
            {"subject": {"name": "Acme Corp", "kind": "organization"}},
            SCREEN,
        ),
        ("resolve", "POST", f"/api/v1/alerts/{a['alert']}/comments", {"body": "note"}, SCREEN),
        ("cases", "GET", "/api/v1/cases", None, SCREEN),
        ("cases", "POST", "/api/v1/cases", {"title": "New case"}, SCREEN),
        ("ops", "GET", "/api/v1/analytics/operations", None, OPS),
        ("ops", "GET", "/api/v1/analytics/usage", None, OPS),
        ("admin", "GET", "/api/v1/admin/users", None, ADMIN),
        ("admin", "GET", "/api/v1/admin/api-keys", None, ADMIN),
        ("admin", "GET", "/api/v1/admin/risk-config", None, ADMIN),
        ("admin", "GET", "/api/v1/billing/plan", None, ADMIN),
        ("audit", "GET", "/api/v1/admin/audit", None, ADMIN),
        ("platform", "GET", "/api/v1/tenants", None, PLATFORM),
        ("platform", "GET", "/api/v1/ingestion/status", None, PLATFORM),
        ("platform", "GET", "/api/v1/admin/audit/verify", None, PLATFORM),
        ("revenue", "GET", "/api/v1/platform/revenue/summary", None, REVENUE),
        ("revenue", "GET", "/api/v1/platform/revenue/mrr", None, REVENUE),
    ]


@pytest.mark.parametrize("role", ROLES)
def test_role_matrix(role: str, make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    client = make_client(role)
    if role == "platform":
        client.headers["X-Tenant-Id"] = str(seed["tenants"]["A"])
    for group, method, path, body, allowed in endpoints(ids):
        # Platform-wide routes must not require a tenant, so the header is blanked for them.
        if group in ("platform", "revenue") and role == "platform":
            reply = client.request(method, path, json=body, headers={"X-Tenant-Id": ""})
        else:
            reply = client.request(method, path, json=body)
        if role in allowed:
            assert reply.status_code < 400, (role, method, path, reply.status_code, reply.text)
        else:
            assert reply.status_code == 403, (role, method, path, reply.status_code)
            assert reply.headers["content-type"].startswith("application/problem+json")


def test_unauthenticated_requests_are_rejected(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    anonymous = make_client()
    for _, method, path, body, _ in endpoints(ids):
        assert anonymous.request(method, path, json=body).status_code == 401, path


def test_denials_are_audited(make_client: Any, seed: dict[str, Any]) -> None:
    client = make_client("a_viewer")
    client.post("/api/v1/subjects", json={"name": "Acme Corp", "kind": "organization"})
    row = (
        seed["admin"]
        .execute("SELECT target FROM audit_log WHERE action = 'authz.denied'")
        .fetchone()
    )
    assert row and row[0] == "/api/v1/subjects"


# --- Cross-tenant access -----------------------------------------------------------------------


def test_tenant_cannot_read_or_change_other_tenant_rows(
    make_client: Any, seed: dict[str, Any]
) -> None:
    ids = populate(seed)
    client = make_client("b_admin")
    foreign = ids["A"]
    for path in (
        f"/api/v1/alerts/{foreign['alert']}",
        f"/api/v1/subjects/{foreign['subject']}",
        f"/api/v1/cases/{foreign['case']}",
    ):
        assert client.get(path).status_code == 404, path
    patch = client.patch(f"/api/v1/alerts/{foreign['alert']}", json={"status": "in_review"})
    assert patch.status_code == 404
    assert (
        client.post(
            f"/api/v1/alerts/{foreign['alert']}/comments", json={"body": "hello"}
        ).status_code
        == 404
    )
    assert (
        client.post("/api/v1/screenings", json={"subject_id": str(foreign["subject"])}).status_code
        == 404
    )
    assert client.post(f"/api/v1/subjects/{foreign['subject']}/monitor", json={}).status_code == 404
    assert client.delete(f"/api/v1/subjects/{foreign['subject']}").status_code == 404
    listing = client.get("/api/v1/alerts").json()["items"]
    assert [row["id"] for row in listing] == [str(ids["B"]["alert"])]
    assert client.get("/api/v1/admin/users").json()["items"][0]["tenant_id"] == str(
        seed["tenants"]["B"]
    )
    emails = {u["email"] for u in client.get("/api/v1/admin/users").json()["items"]}
    assert emails == {"b_analyst@example.org", "b_admin@example.org"}


def test_cannot_attach_foreign_alert_to_case(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    client = make_client("b_analyst")
    reply = client.post(
        "/api/v1/cases", json={"title": "Mixed case", "alert_ids": [str(ids["A"]["alert"])]}
    )
    assert reply.status_code == 404


def test_cannot_assign_alert_to_user_of_another_tenant(
    make_client: Any, seed: dict[str, Any]
) -> None:
    ids = populate(seed)
    client = make_client("a_analyst")
    reply = client.patch(
        f"/api/v1/alerts/{ids['A']['alert']}",
        json={"assignee_id": str(seed["users"]["b_analyst"])},
    )
    assert reply.status_code == 400


def test_foreign_tenant_header_is_ignored_for_tenant_users(
    make_client: Any, seed: dict[str, Any]
) -> None:
    ids = populate(seed)
    client = make_client("a_analyst")
    client.headers["X-Tenant-Id"] = str(seed["tenants"]["B"])
    assert [r["id"] for r in client.get("/api/v1/alerts").json()["items"]] == [
        str(ids["A"]["alert"])
    ]


def test_platform_admin_selects_tenant(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    client = make_client("platform")
    both = {r["id"] for r in client.get("/api/v1/alerts").json()["items"]}
    assert both == {str(ids["A"]["alert"]), str(ids["B"]["alert"])}
    client.headers["X-Tenant-Id"] = str(seed["tenants"]["B"])
    only_b = {r["id"] for r in client.get("/api/v1/alerts").json()["items"]}
    assert only_b == {str(ids["B"]["alert"])}


def test_api_key_is_bound_to_its_tenant(make_client: Any, seed: dict[str, Any]) -> None:
    ids = populate(seed)
    created = make_client("a_admin").post(
        "/api/v1/admin/api-keys", json={"name": "k", "scopes": ["view", "screen"]}
    )
    machine = make_client()
    machine.headers["X-API-Key"] = created.json()["api_key"]
    machine.headers["X-Tenant-Id"] = str(seed["tenants"]["B"])
    items = machine.get("/api/v1/alerts").json()["items"]
    assert [r["id"] for r in items] == [str(ids["A"]["alert"])]


# --- Row Level Security enforced by the database itself ----------------------------------------


def _as_app(db_url: str, tenant: Any = None, platform: bool = False) -> psycopg.Connection[Any]:
    conn = psycopg.connect(_app_url(db_url))
    conn.execute(
        "SELECT set_config('app.tenant_id', %s, true), set_config('app.platform', %s, true)",
        (str(tenant) if tenant else "", "on" if platform else ""),
    )
    return conn


def test_rls_filters_without_application_code(seed: dict[str, Any], db_url: str) -> None:
    populate(seed)
    tables = ["subjects", "alerts", "cases", "users", "screenings", "api_keys", "audit_log"]
    with _as_app(db_url) as no_scope:
        for table in tables:
            assert no_scope.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    with _as_app(db_url, seed["tenants"]["A"]) as scoped:
        tenant_ids = {r[0] for r in scoped.execute("SELECT DISTINCT tenant_id FROM alerts")}
        assert tenant_ids == {seed["tenants"]["A"]}
        assert scoped.execute("SELECT count(*) FROM subjects").fetchone()[0] == 1
        assert scoped.execute("SELECT count(*) FROM tenants").fetchone()[0] == 1
        # Child tables are filtered through their parent.
        assert scoped.execute("SELECT count(*) FROM case_alerts").fetchone()[0] == 1
    with _as_app(db_url, platform=True) as platform:
        assert platform.execute("SELECT count(*) FROM alerts").fetchone()[0] == 2


def test_rls_blocks_writes_across_tenants(seed: dict[str, Any], db_url: str) -> None:
    ids = populate(seed)
    with _as_app(db_url, seed["tenants"]["A"]) as scoped:
        updated = scoped.execute(
            "UPDATE alerts SET status = 'closed' WHERE id = %s", (ids["B"]["alert"],)
        )
        assert updated.rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            scoped.execute(
                "INSERT INTO subjects (tenant_id, kind, name, name_norm) "
                "VALUES (%s, 'person', 'x', 'x')",
                (seed["tenants"]["B"],),
            )


def test_application_role_cannot_change_global_data(seed: dict[str, Any], db_url: str) -> None:
    populate(seed)
    with _as_app(db_url, seed["tenants"]["A"]) as scoped:
        for sql in (
            "DELETE FROM watchlist_entries",
            "UPDATE entities SET name = 'x'",
            "INSERT INTO watchlist_sources (code, name, license, commercial_use, list_type) "
            "VALUES ('x', 'x', 'x', true, 'other')",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                scoped.execute(sql)
            scoped.rollback()
