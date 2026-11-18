"""Celery application: queues ``ingest``, ``nlp``, ``match``, ``reports`` and the beat schedule.

Start a worker with ``make worker`` and the scheduler with ``make beat``. The broker is Valkey
(``ATLAS_CELERY_BROKER_URL``). The worker connects as the ``atlas_worker`` database role.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from celery import Celery
from celery.schedules import crontab

from atlas.api.settings import Settings, from_env

BROKER = os.environ.get("ATLAS_CELERY_BROKER_URL", "redis://127.0.0.1:6379/1")

app = Celery("atlas", broker=BROKER, backend=None, include=["atlas.jobs.tasks"])
app.conf.update(
    task_default_queue="match",
    task_routes={
        "atlas.process_batch": {"queue": "match"},
        "atlas.monitor_scan": {"queue": "match"},
        "atlas.ingest_article": {"queue": "ingest"},
        "atlas.refresh_alert_stats": {"queue": "reports"},
        "atlas.refresh_business_views": {"queue": "reports"},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=3600,
    timezone="UTC",
    beat_schedule={
        "monitor-subjects": {"task": "atlas.monitor_scan", "schedule": 300.0},
        "alert-statistics": {"task": "atlas.refresh_alert_stats", "schedule": 900.0},
        "business-views-nightly": {
            "task": "atlas.refresh_business_views",
            "schedule": crontab(hour=2, minute=0),
        },
    },
)


def worker_settings() -> Settings:
    base = from_env()
    return replace(base, database_url=base.worker_database_url)


def send(task: str, *args: Any) -> None:
    app.send_task(task, args=list(args))
