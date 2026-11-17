"""Process-wide objects shared by requests (settings, pool, limiter, event bus, mailer)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from argon2 import PasswordHasher

from atlas.api.db import Database
from atlas.api.mailer import Mailer
from atlas.api.ratelimit import RateLimiter
from atlas.api.settings import Settings


class Bus(Protocol):
    def publish(self, tenant_id: str, event: dict[str, Any]) -> None: ...

    def subscribe(self, tenant_id: str) -> Any: ...


@dataclass
class AppState:
    settings: Settings
    db: Database
    hasher: PasswordHasher
    limiter: RateLimiter
    bus: Bus
    mailer: Mailer
    decoy_hash: str = ""
    extras: dict[str, Any] = field(default_factory=dict)
