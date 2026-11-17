"""Connection pool and per-request database sessions bound to a tenant scope."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from atlas.api.settings import Settings

Row = dict[str, Any]


@dataclass(frozen=True)
class Scope:
    """Row Level Security scope: one tenant, or the platform (all tenants)."""

    tenant_id: uuid.UUID | None = None
    platform: bool = False


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=settings.pool_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def session(self, scope: Scope) -> Iterator[psycopg.Connection[Row]]:
        """One transaction with the RLS settings applied as transaction-local values."""
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "SELECT set_config('app.tenant_id', %s, true), "
                    "set_config('app.platform', %s, true)",
                    (
                        str(scope.tenant_id) if scope.tenant_id else "",
                        "on" if scope.platform else "",
                    ),
                )
                yield cast("psycopg.Connection[Row]", conn)

    def ready(self) -> bool:
        try:
            with self.pool.connection(timeout=3) as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False


PLATFORM = Scope(platform=True)
