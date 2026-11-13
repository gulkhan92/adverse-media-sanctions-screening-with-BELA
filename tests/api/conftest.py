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


CLEAN = (
    "TRUNCATE watchlist_sources, entities, articles, entity_embeddings "
    "RESTART IDENTITY CASCADE"
)


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
