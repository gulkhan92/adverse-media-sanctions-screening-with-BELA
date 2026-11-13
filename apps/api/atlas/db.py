"""Database helpers: psycopg connection and bulk COPY utilities."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import psycopg

from atlas.config import DATABASE_URL


def connect(autocommit: bool = False) -> psycopg.Connection[Any]:
    return psycopg.connect(DATABASE_URL, autocommit=autocommit)


def copy_rows(
    conn: psycopg.Connection[Any], table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]
) -> int:
    """Stream rows into ``table`` with COPY. Returns the number of rows written."""
    count = 0
    cols = ", ".join(columns)
    with conn.cursor() as cur, cur.copy(f"COPY {table} ({cols}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    return count


def scalar(conn: psycopg.Connection[Any], sql: str, params: Sequence[Any] = ()) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None
