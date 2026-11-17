"""Celery tasks. Each task builds the application state once per worker process."""

from __future__ import annotations

import uuid
from typing import Any

from atlas.api.app import build_state
from atlas.api.batch import process_batch
from atlas.api.monitoring import refresh_alert_stats, scan_monitored
from atlas.api.state import AppState
from atlas.jobs.celery_app import app, worker_settings

_state: AppState | None = None


def state() -> AppState:
    global _state
    if _state is None:
        _state = build_state(worker_settings())
    return _state


@app.task(name="atlas.process_batch")  # type: ignore[untyped-decorator]
def process_batch_task(tenant_id: str, batch_id: str) -> None:
    process_batch(state(), uuid.UUID(tenant_id), uuid.UUID(batch_id))


@app.task(name="atlas.monitor_scan")  # type: ignore[untyped-decorator]
def monitor_scan_task(tenant_id: str | None = None) -> dict[str, Any]:
    return scan_monitored(state(), uuid.UUID(tenant_id) if tenant_id else None)


@app.task(name="atlas.refresh_alert_stats")  # type: ignore[untyped-decorator]
def refresh_alert_stats_task() -> int:
    return refresh_alert_stats(state())
