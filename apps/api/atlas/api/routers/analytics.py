"""Operational analytics for a tenant and platform revenue analytics (synthetic demo data)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from atlas.api.deps import Authorized, Ctx
from atlas.api.errors import PROBLEM_RESPONSES
from atlas.api.rbac import OPS, VIEW

analytics = APIRouter(prefix="/analytics", tags=["analytics"], responses=PROBLEM_RESPONSES)


@analytics.get("/overview", summary="Alert and screening counts")
def overview(
    days: int = Query(default=30, ge=1, le=365), ctx: Ctx = Authorized(VIEW)
) -> dict[str, Any]:
    c = ctx.conn
    by_status = c.execute(
        "SELECT status, count(*) AS n FROM alerts "
        "WHERE created_at > now() - make_interval(days => %s) "
        "GROUP BY status",
        (days,),
    ).fetchall()
    by_severity = c.execute(
        "SELECT severity, count(*) AS n FROM alerts "
        "WHERE created_at > now() - make_interval(days => %s) "
        "GROUP BY severity",
        (days,),
    ).fetchall()
    screenings = c.execute(
        "SELECT count(*) AS n, count(*) FILTER (WHERE risk_level IN ('high', 'critical')) AS high "
        "FROM screenings WHERE created_at > now() - make_interval(days => %s)",
        (days,),
    ).fetchone()
    monitored = c.execute(
        "SELECT count(*) AS n FROM subjects WHERE monitored AND deleted_at IS NULL"
    ).fetchone()
    return {
        "window_days": days,
        "alerts_by_status": by_status,
        "alerts_by_severity": by_severity,
        "screenings": screenings,
        "monitored_subjects": monitored["n"] if monitored else 0,
    }


@analytics.get("/operations", summary="Review workload and turnaround")
def operations(
    days: int = Query(default=30, ge=1, le=365), ctx: Ctx = Authorized(OPS)
) -> dict[str, Any]:
    c = ctx.conn
    resolved = c.execute(
        "SELECT count(*) AS resolved, "
        "percentile_cont(0.5) WITHIN GROUP "
        "(ORDER BY extract(epoch FROM resolved_at - created_at) / 60) "
        "AS median_review_minutes, "
        "count(*) FILTER (WHERE status = 'true_match') AS true_match, "
        "count(*) FILTER (WHERE status = 'false_positive') AS false_positive "
        "FROM alerts WHERE resolved_at > now() - make_interval(days => %s)",
        (days,),
    ).fetchone()
    backlog = c.execute(
        "SELECT status, count(*) AS n FROM alerts "
        "WHERE status IN ('new', 'in_review', 'escalated') "
        "GROUP BY status"
    ).fetchall()
    per_day = c.execute(
        "SELECT date_trunc('day', created_at)::date AS day, count(*) AS alerts FROM alerts "
        "WHERE created_at > now() - make_interval(days => %s) GROUP BY 1 ORDER BY 1",
        (days,),
    ).fetchall()
    return {
        "window_days": days,
        "resolved": resolved,
        "backlog": backlog,
        "alerts_per_day": per_day,
    }


@analytics.get("/usage", summary="Metered usage")
def usage(
    days: int = Query(default=30, ge=1, le=365), ctx: Ctx = Authorized(OPS)
) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT day, screenings, batch_rows, api_calls, articles_matched FROM usage_daily "
        "WHERE day > current_date - %s ORDER BY day",
        (days,),
    ).fetchall()
    return {"window_days": days, "items": rows}
