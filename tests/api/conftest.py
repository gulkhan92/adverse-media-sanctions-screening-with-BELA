from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

ADMIN_URL = os.environ.get("ATLAS_ADMIN_URL", "postgresql://atlas@127.0.0.1:54329/postgres")
TEST_DB = "atlas_test"
API_DIR = Path(__file__).resolve().parents[2] / "apps" / "api"


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    """Create a throwaway database, apply all migrations, and drop it afterwards."""
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not reachable (start it with infra/dev_db.sh start)")
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"
    env = {**os.environ, "DATABASE_URL": url, "PYTHONPATH": str(API_DIR)}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    yield url
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.close()


CLEAN = "TRUNCATE watchlist_sources, entities, articles, entity_embeddings RESTART IDENTITY CASCADE"


@pytest.fixture()
def conn(db_url: str) -> Iterator[psycopg.Connection[Any]]:
    connection = psycopg.connect(db_url)
    connection.execute(CLEAN)
    connection.commit()
    yield connection
    connection.rollback()
    connection.execute(CLEAN)
    connection.commit()
    connection.close()


# --- API fixtures ------------------------------------------------------------------------------

PASSWORD = "Correct-Horse-Battery-Staple-42"
MFA_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
ROLES = {
    "a_viewer": ("A", "viewer"),
    "a_analyst": ("A", "analyst"),
    "a_manager": ("A", "manager"),
    "a_admin": ("A", "tenant_admin"),
    "b_analyst": ("B", "analyst"),
    "b_admin": ("B", "tenant_admin"),
    "finance": (None, "finance_analyst"),
    "platform": (None, "platform_admin"),
}
RESET = (
    "TRUNCATE tenants, watchlist_sources, entities, articles, plans, contact_requests, "
    "newsletter_subscribers, "
    "monitoring_runs, alert_stats_daily "
    "RESTART IDENTITY CASCADE"
)


def _app_url(url: str) -> str:
    return url.replace("://atlas@", "://atlas_app@")


@pytest.fixture(scope="session")
def api_app(db_url: str) -> Iterator[Any]:
    from atlas.api.app import create_app
    from atlas.api.settings import Settings
    from fastapi.testclient import TestClient

    settings = Settings(
        database_url=_app_url(db_url),
        argon_time_cost=1,
        argon_memory_kib=8192,
        argon_parallelism=1,
        limit_ip=(100000, 60),
        limit_user=(100000, 60),
        limit_api_key=(100000, 60),
    )
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as client:
        yield app, client


@pytest.fixture()
def seed(api_app: Any, db_url: str) -> Iterator[dict[str, Any]]:
    """Two tenants plus one user per role. Admin roles have MFA enrolled."""
    import uuid

    from atlas.api.security import make_hasher

    app, _ = api_app
    state = app.state.atlas
    state.limiter.reset()
    state.mailer.outbox.clear()
    hasher = make_hasher(state.settings)
    admin = psycopg.connect(db_url, autocommit=True)
    for trigger in ("audit_no_update", "audit_no_truncate"):
        admin.execute(f"ALTER TABLE audit_log DISABLE TRIGGER {trigger}")
    admin.execute("TRUNCATE audit_log RESTART IDENTITY")
    admin.execute(RESET)
    for trigger in ("audit_no_update", "audit_no_truncate"):
        admin.execute(f"ALTER TABLE audit_log ENABLE TRIGGER {trigger}")
    tenants = {
        name: admin.execute(
            "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"Tenant {name}",)
        ).fetchone()[0]  # type: ignore[index]
        for name in ("A", "B")
    }
    password_hash = hasher.hash(PASSWORD)
    users: dict[str, uuid.UUID] = {}
    for key, (tenant, role) in ROLES.items():
        users[key] = admin.execute(
            "INSERT INTO users (tenant_id, email, password_hash, role) VALUES (%s, %s, %s, %s) "
            "RETURNING id",
            (tenants.get(tenant) if tenant else None, f"{key}@example.org", password_hash, role),
        ).fetchone()[0]  # type: ignore[index]
        if role in ("tenant_admin", "platform_admin"):
            admin.execute(
                "INSERT INTO user_mfa (user_id, totp_secret_enc, recovery_hashes, enabled_at) "
                "VALUES (%s, pgp_sym_encrypt(%s, %s), %s, now())",
                (users[key], MFA_SECRET, state.settings.data_key, []),
            )
    yield {"tenants": tenants, "users": users, "admin": admin, "app": app, "state": state}
    admin.close()


@pytest.fixture()
def make_client(api_app: Any, seed: dict[str, Any]) -> Any:
    from fastapi.testclient import TestClient

    _, _ = api_app

    def build(key: str | None = None) -> Any:
        client = TestClient(seed["app"], base_url="https://testserver")
        if key is None:
            return client
        import pyotp

        reply = client.post(
            "/api/v1/auth/login", json={"email": f"{key}@example.org", "password": PASSWORD}
        )
        assert reply.status_code == 200, reply.text
        body = reply.json()
        if body["status"] == "mfa_required":
            reply = client.post(
                "/api/v1/auth/mfa/verify",
                json={"mfa_token": body["mfa_token"], "code": pyotp.TOTP(MFA_SECRET).now()},
            )
            assert reply.status_code == 200, reply.text
        client.headers["X-CSRF-Token"] = client.cookies.get("atlas_csrf")
        return client

    return build


@pytest.fixture()
def worker(seed: dict[str, Any], db_url: str) -> Iterator[Any]:
    """Application state for the worker role, sharing the API's event bus."""
    from dataclasses import replace

    from atlas.api.app import build_state

    api_state = seed["state"]
    settings = replace(
        api_state.settings, database_url=db_url.replace("://atlas@", "://atlas_worker@")
    )
    state = build_state(settings)
    state.bus = api_state.bus
    yield state
    state.db.close()
