"""Audit log writes. The hash chain is computed by a database trigger (migration 0004)."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

SENSITIVE_KEYS = ("password", "token", "secret", "code", "authorization", "cookie", "key")


def scrub(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Drop values that must never be logged."""
    clean: dict[str, Any] = {}
    for name, value in (detail or {}).items():
        if any(part in name.lower() for part in SENSITIVE_KEYS) and name != "key_prefix":
            clean[name] = "[redacted]"
        elif isinstance(value, dict):
            clean[name] = scrub(value)
        else:
            clean[name] = value
    return clean


def write_audit(
    conn: psycopg.Connection[Any],
    action: str,
    *,
    tenant_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    target: str | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (tenant_id, actor_id, action, target, detail, request_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (tenant_id, actor_id, action, target, Jsonb(scrub(detail)), request_id),
    )
