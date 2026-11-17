"""Opaque keyset cursors."""

from __future__ import annotations

import base64
import json
from typing import Any

from atlas.api.errors import bad_request

MAX_LIMIT = 100


def encode_cursor(values: list[Any]) -> str:
    raw = json.dumps(values, default=str, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None, size: int) -> list[Any] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, json.JSONDecodeError):
        raise bad_request("The cursor is not valid.", "invalid-cursor") from None
    if not isinstance(values, list) or len(values) != size:
        raise bad_request("The cursor is not valid.", "invalid-cursor")
    return values


def clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))
