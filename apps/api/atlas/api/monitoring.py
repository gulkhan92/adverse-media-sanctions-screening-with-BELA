"""Continuous monitoring: re-evaluate monitored subjects and create de-duplicated alerts.

An alert is created for each (subject, article, list entry) combination whose own score reaches
the tenant's medium threshold. The unique index ``ux_alerts_dedup`` makes creation idempotent,
so repeated scans and overlapping triggers never produce duplicates. Alert scores use only the
evidence of that alert, and the stored breakdown shows each component.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from atlas.api.db import PLATFORM, Row, Scope
from atlas.api.scoring import compute_score
from atlas.api.screening import evaluate, load_config, subject_secrets
from atlas.api.state import AppState
from atlas.nlp.narrative import Evidence, template_narrative

log = logging.getLogger("atlas.api.monitoring")
ALERT_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _narrative_for(subject: Row, watch: dict[str, Any] | None, item: dict[str, Any] | None) -> str:
    if item is not None:
        return template_narrative(
            Evidence(
                subject=subject["name"],
                match_type="adverse media mention",
                category=item["category"],
                severity=item["severity"],
                sentence=item["evidence"],
                source=item["article"].get("domain") or "news source",
                link_confidence=item["link_confidence"],
            )
        )
    assert watch is not None
    programs = ", ".join(watch["programs"][:3]) or "no program listed"
    return (
        f"{subject['name']} resembles the {watch['source']} list entry {watch['name']} "
        f"(programs: {programs}) with name confidence {watch['confidence']:.2f}. "
        "This is a lead for review, not a determination."
    )


def _create_alert(
    conn: psycopg.Connection[Row],
    tenant_id: uuid.UUID,
    subject: Row,
    watch: dict[str, Any] | None,
    item: dict[str, Any] | None,
    breakdown: dict[str, Any],
) -> dict[str, Any] | None:
    row = conn.execute(
        "INSERT INTO alerts (tenant_id, subject_id, entry_id, article_id, mention_id, severity, "
        "narrative, score, score_breakdown) VALUES (%s, %s, %s, "
        "(SELECT article_id FROM mentions WHERE id = %s), %s, %s, %s, %s, %s) "
        "ON CONFLICT DO NOTHING RETURNING id, created_at",
        (
            tenant_id,
            subject["id"],
            watch["entry_id"] if watch else (item or {}).get("entry_id"),
            item["mention_id"] if item else None,
            item["mention_id"] if item else None,
            breakdown["level"],
            _narrative_for(subject, watch, item),
            breakdown["score"],
            Jsonb(breakdown),
        ),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "INSERT INTO alert_events (alert_id, event_type, to_status, reason) "
        "VALUES (%s, 'created', 'new', 'monitoring')",
        (row["id"],),
    )
    return {"id": row["id"], "severity": breakdown["level"], "score": breakdown["score"]}


def scan_subject(
    conn: psycopg.Connection[Row],
    tenant_id: uuid.UUID,
    subject: Row,
    key: str,
    embedder: Any = None,
) -> list[dict[str, Any]]:
    """Evaluate one subject and create any new alerts. Returns the alerts created."""
    config, _ = load_config(conn, tenant_id)
    dob, identifiers = subject_secrets(conn, subject["id"], key)
    result = evaluate(conn, subject, dob, config, identifiers, embedder)
    minimum = ALERT_RANK["medium"]
    created: list[dict[str, Any]] = []
    watch_by_entry = {w["entry_id"]: w for w in result["watch"]}
    covered_entries: set[int] = set()
    for item in result["adverse"]:
        watch = watch_by_entry.get(item.get("entry_id"))
        breakdown = compute_score(config, watch, [item])
        if ALERT_RANK[breakdown["level"]] < minimum:
            continue
        if watch:
            covered_entries.add(watch["entry_id"])
        alert = _create_alert(conn, tenant_id, subject, watch, item, breakdown)
        if alert:
            created.append(alert)
    for watch in result["watch"]:
        breakdown = compute_score(config, watch, [])
        if ALERT_RANK[breakdown["level"]] < minimum:
            continue
        alert = _create_alert(conn, tenant_id, subject, watch, None, breakdown)
        if alert:
            created.append(alert)
    return created


def scan_monitored(
    state: AppState, tenant_id: uuid.UUID | None = None, only_if_new: bool = True
) -> dict[str, Any]:
    """Scan monitored subjects (one tenant or all). Publishes each new alert to the tenant."""
    key = state.settings.data_key
    embedder = state.extras.get("embedder")
    with state.db.session(PLATFORM) as conn:
        last = conn.execute(
            "SELECT last_mention_id, last_entry_id FROM monitoring_runs ORDER BY id DESC LIMIT 1"
        ).fetchone() or {"last_mention_id": 0, "last_entry_id": 0}
        top = conn.execute(
            "SELECT coalesce((SELECT max(id) FROM mentions), 0) AS m, "
            "coalesce((SELECT max(id) FROM watchlist_entries), 0) AS e"
        ).fetchone()
        assert top is not None
        run = conn.execute("INSERT INTO monitoring_runs DEFAULT VALUES RETURNING id").fetchone()
        assert run is not None
        subjects = conn.execute(
            "SELECT id, tenant_id, name, kind, country, name_norm FROM subjects "
            "WHERE monitored AND deleted_at IS NULL AND (%s::uuid IS NULL OR tenant_id = %s) "
            "ORDER BY tenant_id, created_at",
            (tenant_id, tenant_id),
        ).fetchall()
    changed = top["m"] > last["last_mention_id"] or top["e"] > last["last_entry_id"]
    created_total = 0
    scanned = 0
    if changed or not only_if_new:
        for subject in subjects:
            with state.db.session(Scope(tenant_id=subject["tenant_id"])) as conn:
                created = scan_subject(conn, subject["tenant_id"], subject, key, embedder)
            scanned += 1
            created_total += len(created)
            for alert in created:
                state.bus.publish(
                    str(subject["tenant_id"]),
                    {
                        "type": "alert.created",
                        "alert_id": str(alert["id"]),
                        "severity": alert["severity"],
                        "subject": subject["name"],
                    },
                )
    with state.db.session(PLATFORM) as conn:
        conn.execute(
            "UPDATE monitoring_runs SET finished_at = now(), last_mention_id = %s, "
            "last_entry_id = %s, subjects = %s, alerts_created = %s WHERE id = %s",
            (top["m"], top["e"], scanned, created_total, run["id"]),
        )
    return {
        "run_id": run["id"],
        "subjects_scanned": scanned,
        "alerts_created": created_total,
        "skipped_no_new_data": not (changed or not only_if_new),
    }


def refresh_alert_stats(state: AppState, days: int = 2) -> int:
    """Recompute daily alert statistics from alert history (all tenants).

    Only the alert columns are written; the latency columns of the same rows are left alone.
    """
    with state.db.session(PLATFORM) as conn:
        rows = conn.execute(
            "SELECT d::date AS day, "
            " (SELECT count(*) FROM alerts a WHERE a.created_at::date = d::date) AS alerts, "
            " (SELECT count(*) FROM alerts a WHERE a.resolved_at::date = d::date "
            "   AND a.status = 'true_match') AS confirmed_true, "
            " (SELECT count(*) FROM alerts a WHERE a.resolved_at::date = d::date "
            "   AND a.status = 'false_positive') AS false_positive, "
            " (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY "
            "   extract(epoch FROM a.resolved_at - a.created_at) / 60) FROM alerts a "
            "   WHERE a.resolved_at::date = d::date) AS median_review_minutes "
            "FROM generate_series(current_date - %s, current_date, interval '1 day') d",
            (days - 1,),
        ).fetchall()
        for r in rows:
            conn.execute(
                "INSERT INTO alert_stats_daily (day, alerts, confirmed_true, false_positive, "
                "median_review_minutes) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (day) DO UPDATE "
                "SET alerts = EXCLUDED.alerts, confirmed_true = EXCLUDED.confirmed_true, "
                "false_positive = EXCLUDED.false_positive, "
                "median_review_minutes = EXCLUDED.median_review_minutes",
                (
                    r["day"],
                    r["alerts"],
                    r["confirmed_true"],
                    r["false_positive"],
                    r["median_review_minutes"],
                ),
            )
    return len(rows)


def today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()
