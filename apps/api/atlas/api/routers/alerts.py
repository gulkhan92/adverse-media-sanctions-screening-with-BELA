"""Alert queue, alert review, comments, and case management."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query
from psycopg.types.json import Jsonb

from atlas.api.deps import Authorized, Ctx
from atlas.api.errors import PROBLEM_RESPONSES, bad_request, not_found
from atlas.api.pagination import clamp_limit, decode_cursor, encode_cursor
from atlas.api.rbac import CASES, RESOLVE, VIEW
from atlas.api.schemas import DISCLAIMER, AlertPatch, CaseIn, CasePatch, CommentIn

alerts = APIRouter(prefix="/alerts", tags=["alerts"], responses=PROBLEM_RESPONSES)
cases = APIRouter(prefix="/cases", tags=["cases"], responses=PROBLEM_RESPONSES)

TRANSITIONS: dict[str, set[str]] = {
    "new": {"in_review", "escalated", "true_match", "false_positive", "closed"},
    "in_review": {"new", "escalated", "true_match", "false_positive", "closed"},
    "escalated": {"in_review", "true_match", "false_positive", "closed"},
    "true_match": {"in_review", "closed"},
    "false_positive": {"in_review", "closed"},
    "closed": {"in_review"},
}
DECISIONS = {"true_match", "false_positive", "closed", "escalated"}
SEVERITY_ORDER = (
    "CASE a.severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END"
)


@alerts.get("", summary="Alert queue")
def list_alerts(
    status: str | None = Query(
        default=None, pattern="^(new|in_review|escalated|true_match|false_positive|closed)$"
    ),
    severity: str | None = Query(default=None, pattern="^(low|medium|high|critical)$"),
    subject_id: uuid.UUID | None = None,
    assignee_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = 25,
    ctx: Ctx = Authorized(VIEW),
) -> dict[str, Any]:
    limit = clamp_limit(limit)
    after = decode_cursor(cursor, 2)
    sql = (
        "SELECT a.id, a.subject_id, s.name AS subject_name, a.severity, a.status, a.assignee_id, "
        "a.created_at, a.resolved_at, w.name AS watchlist_name FROM alerts a "
        "JOIN subjects s ON s.id = a.subject_id "
        "LEFT JOIN watchlist_entries w ON w.id = a.entry_id WHERE true"
    )
    params: list[Any] = []
    for column, value in (
        ("a.status", status),
        ("a.severity", severity),
        ("a.subject_id", subject_id),
        ("a.assignee_id", assignee_id),
    ):
        if value is not None:
            sql += f" AND {column} = %s"
            params.append(value)
    if after:
        sql += " AND (a.created_at, a.id) < (%s::timestamptz, %s::uuid)"
        params += after
    sql += " ORDER BY a.created_at DESC, a.id DESC LIMIT %s"
    rows = ctx.conn.execute(sql, [*params, limit + 1]).fetchall()
    page = rows[:limit]
    nxt = (
        encode_cursor([page[-1]["created_at"].isoformat(), str(page[-1]["id"])])
        if len(rows) > limit
        else None
    )
    return {"items": page, "next_cursor": nxt}


def _alert(ctx: Ctx, alert_id: uuid.UUID) -> dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT a.*, s.name AS subject_name, s.kind AS subject_kind, w.name AS watchlist_name, "
        "w.programs, src.code AS source, ar.title AS article_title, ar.url AS article_url, "
        "ar.published_at AS article_published_at FROM alerts a "
        "JOIN subjects s ON s.id = a.subject_id "
        "LEFT JOIN watchlist_entries w ON w.id = a.entry_id "
        "LEFT JOIN watchlist_sources src ON src.id = w.source_id "
        "LEFT JOIN articles ar ON ar.id = a.article_id WHERE a.id = %s",
        (alert_id,),
    ).fetchone()
    if row is None:
        raise not_found("Alert")
    return row


@alerts.get("/{alert_id}", summary="Alert detail with history")
def get_alert(alert_id: uuid.UUID, ctx: Ctx = Authorized(VIEW)) -> dict[str, Any]:
    row = _alert(ctx, alert_id)
    events = ctx.conn.execute(
        "SELECT e.id, e.event_type, e.from_status, e.to_status, e.reason, e.created_at, "
        "u.email AS actor FROM alert_events e LEFT JOIN users u ON u.id = e.actor_id "
        "WHERE e.alert_id = %s ORDER BY e.created_at, e.id",
        (alert_id,),
    ).fetchall()
    return {**row, "events": events, "disclaimer": DISCLAIMER}


@alerts.patch("/{alert_id}", summary="Change status or assignee")
def patch_alert(
    alert_id: uuid.UUID, body: AlertPatch, ctx: Ctx = Authorized(RESOLVE)
) -> dict[str, Any]:
    current = _alert(ctx, alert_id)
    if body.status is None and body.assignee_id is None:
        raise bad_request("Provide status or assignee_id.")
    if body.assignee_id is not None:
        assignee = ctx.conn.execute(
            "SELECT 1 FROM users WHERE id = %s AND tenant_id = %s AND is_active",
            (body.assignee_id, current["tenant_id"]),
        ).fetchone()
        if assignee is None:
            raise bad_request("The assignee is not a user of this tenant.", "invalid-assignee")
        ctx.conn.execute(
            "UPDATE alerts SET assignee_id = %s WHERE id = %s", (body.assignee_id, alert_id)
        )
        ctx.conn.execute(
            "INSERT INTO alert_events (alert_id, actor_id, event_type, reason) "
            "VALUES (%s, %s, 'assigned', %s)",
            (alert_id, ctx.principal.user_id, str(body.assignee_id)),
        )
    if body.status is not None and body.status != current["status"]:
        if body.status not in TRANSITIONS[current["status"]]:
            raise bad_request(
                f"Cannot move an alert from {current['status']} to {body.status}.",
                "invalid-transition",
            )
        if body.status in DECISIONS and len((body.reason or "").strip()) < 3:
            raise bad_request("A reviewer reason is required for this decision.", "reason-required")
        ctx.conn.execute(
            "UPDATE alerts SET status = %s, resolved_at = CASE WHEN %s IN "
            "('true_match', 'false_positive', 'closed') THEN now() ELSE NULL END WHERE id = %s",
            (body.status, body.status, alert_id),
        )
        ctx.conn.execute(
            "INSERT INTO alert_events (alert_id, actor_id, event_type, from_status, to_status, "
            "reason) VALUES (%s, %s, 'status_change', %s, %s, %s)",
            (alert_id, ctx.principal.user_id, current["status"], body.status, body.reason),
        )
        if body.status in ("true_match", "false_positive"):
            # Reviewer decisions are labeled data for later calibration of the score.
            ctx.conn.execute(
                "INSERT INTO alert_feedback (tenant_id, alert_id, label, score, features, "
                "reviewer_id) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    current["tenant_id"],
                    alert_id,
                    body.status,
                    current["score"],
                    Jsonb(current["score_breakdown"] or {}),
                    ctx.principal.user_id,
                ),
            )
    ctx.audit(
        "alert.updated",
        str(alert_id),
        {"from": current["status"], "to": body.status, "assignee": str(body.assignee_id or "")},
    )
    updated = _alert(ctx, alert_id)
    ctx.publish(
        {
            "type": "alert.updated",
            "alert_id": str(alert_id),
            "status": updated["status"],
            "severity": updated["severity"],
        }
    )
    return updated


@alerts.post("/{alert_id}/comments", status_code=201, summary="Comment on an alert")
def comment_alert(
    alert_id: uuid.UUID, body: CommentIn, ctx: Ctx = Authorized(RESOLVE)
) -> dict[str, Any]:
    _alert(ctx, alert_id)
    row = ctx.conn.execute(
        "INSERT INTO alert_events (alert_id, actor_id, event_type, reason) "
        "VALUES (%s, %s, 'comment', %s) RETURNING id, created_at",
        (alert_id, ctx.principal.user_id, body.body),
    ).fetchone()
    assert row is not None
    ctx.audit("alert.commented", str(alert_id))
    ctx.publish({"type": "alert.commented", "alert_id": str(alert_id)})
    return {
        "id": row["id"],
        "alert_id": alert_id,
        "body": body.body,
        "created_at": row["created_at"],
    }


# --- Cases -------------------------------------------------------------------------------------


def _case(ctx: Ctx, case_id: uuid.UUID) -> dict[str, Any]:
    row = ctx.conn.execute(
        "SELECT c.id, c.title, c.status, c.owner_id, c.due_date, c.created_at FROM cases c "
        "WHERE c.id = %s",
        (case_id,),
    ).fetchone()
    if row is None:
        raise not_found("Case")
    row["alerts"] = ctx.conn.execute(
        "SELECT a.id, a.severity, a.status, s.name AS subject_name FROM case_alerts ca "
        "JOIN alerts a ON a.id = ca.alert_id JOIN subjects s ON s.id = a.subject_id "
        "WHERE ca.case_id = %s ORDER BY a.created_at DESC",
        (case_id,),
    ).fetchall()
    row["entities"] = ctx.conn.execute(
        "SELECT e.id, e.qid, e.name, e.kind FROM case_entities ce "
        "JOIN entities e ON e.id = ce.entity_id WHERE ce.case_id = %s ORDER BY e.name",
        (case_id,),
    ).fetchall()
    row["notes"] = ctx.conn.execute(
        "SELECT n.id, n.body, n.created_at, u.email AS author FROM case_notes n "
        "LEFT JOIN users u ON u.id = n.author_id WHERE n.case_id = %s ORDER BY n.created_at, n.id",
        (case_id,),
    ).fetchall()
    return row


def _member(ctx: Ctx, user_id: uuid.UUID | None) -> None:
    if user_id is None:
        return
    found = ctx.conn.execute(
        "SELECT 1 FROM users WHERE id = %s AND tenant_id = %s AND is_active",
        (user_id, ctx.tenant_id),
    ).fetchone()
    if found is None:
        raise bad_request("The owner is not a user of this tenant.", "invalid-owner")


def _attach_entities(ctx: Ctx, case_id: uuid.UUID, entity_ids: list[int]) -> None:
    for entity_id in entity_ids:
        found = ctx.conn.execute("SELECT 1 FROM entities WHERE id = %s", (entity_id,)).fetchone()
        if found is None:
            raise not_found(f"Entity {entity_id}")
        ctx.conn.execute(
            "INSERT INTO case_entities (case_id, entity_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (case_id, entity_id),
        )


def _attach(ctx: Ctx, case_id: uuid.UUID, alert_ids: list[uuid.UUID]) -> None:
    for alert_id in alert_ids:
        found = ctx.conn.execute("SELECT 1 FROM alerts WHERE id = %s", (alert_id,)).fetchone()
        if found is None:
            raise not_found(f"Alert {alert_id}")
        ctx.conn.execute(
            "INSERT INTO case_alerts (case_id, alert_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (case_id, alert_id),
        )


@cases.get("", summary="List cases")
def list_cases(
    status: str | None = Query(default=None, pattern="^(open|in_progress|closed)$"),
    cursor: str | None = None,
    limit: int = 25,
    ctx: Ctx = Authorized(CASES),
) -> dict[str, Any]:
    limit = clamp_limit(limit)
    after = decode_cursor(cursor, 2)
    sql = (
        "SELECT c.id, c.title, c.status, c.owner_id, c.due_date, c.created_at, "
        "(SELECT count(*) FROM case_alerts ca WHERE ca.case_id = c.id) AS alert_count "
        "FROM cases c WHERE true"
    )
    params: list[Any] = []
    if status:
        sql += " AND c.status = %s"
        params.append(status)
    if after:
        sql += " AND (c.created_at, c.id) < (%s::timestamptz, %s::uuid)"
        params += after
    sql += " ORDER BY c.created_at DESC, c.id DESC LIMIT %s"
    rows = ctx.conn.execute(sql, [*params, limit + 1]).fetchall()
    page = rows[:limit]
    nxt = (
        encode_cursor([page[-1]["created_at"].isoformat(), str(page[-1]["id"])])
        if len(rows) > limit
        else None
    )
    return {"items": page, "next_cursor": nxt}


@cases.post("", status_code=201, summary="Open a case")
def create_case(body: CaseIn, ctx: Ctx = Authorized(CASES)) -> dict[str, Any]:
    _member(ctx, body.owner_id)
    row = ctx.conn.execute(
        "INSERT INTO cases (tenant_id, title, owner_id, due_date) VALUES (%s, %s, %s, %s) "
        "RETURNING id",
        (ctx.tenant_id, body.title, body.owner_id or ctx.principal.user_id, body.due_date),
    ).fetchone()
    assert row is not None
    _attach(ctx, row["id"], body.alert_ids)
    ctx.audit("case.created", str(row["id"]))
    return _case(ctx, row["id"])


@cases.get("/{case_id}", summary="Case detail")
def get_case(case_id: uuid.UUID, ctx: Ctx = Authorized(CASES)) -> dict[str, Any]:
    return _case(ctx, case_id)


@cases.patch("/{case_id}", summary="Update a case")
def patch_case(case_id: uuid.UUID, body: CasePatch, ctx: Ctx = Authorized(CASES)) -> dict[str, Any]:
    current = _case(ctx, case_id)
    _member(ctx, body.owner_id)
    data = body.model_dump(exclude_unset=True)
    ctx.conn.execute(
        "UPDATE cases SET title = coalesce(%s, title), status = coalesce(%s, status), "
        "owner_id = coalesce(%s, owner_id), due_date = coalesce(%s, due_date) WHERE id = %s",
        (
            data.get("title"),
            data.get("status"),
            data.get("owner_id"),
            data.get("due_date"),
            case_id,
        ),
    )
    _attach(ctx, case_id, body.add_alert_ids)
    _attach_entities(ctx, case_id, body.add_entity_ids)
    for entity_id in body.remove_entity_ids:
        ctx.conn.execute(
            "DELETE FROM case_entities WHERE case_id = %s AND entity_id = %s", (case_id, entity_id)
        )
    for alert_id in body.remove_alert_ids:
        ctx.conn.execute(
            "DELETE FROM case_alerts WHERE case_id = %s AND alert_id = %s", (case_id, alert_id)
        )
    if body.note:
        ctx.conn.execute(
            "INSERT INTO case_notes (case_id, author_id, body) VALUES (%s, %s, %s)",
            (case_id, ctx.principal.user_id, body.note),
        )
    ctx.audit("case.updated", str(case_id), {"fields": sorted(data), "was": current["status"]})
    return _case(ctx, case_id)
