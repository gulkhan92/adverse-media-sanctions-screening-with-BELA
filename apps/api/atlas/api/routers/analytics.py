"""Operational analytics for a tenant and platform revenue analytics (synthetic demo data)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from atlas.api.deps import Authorized, Ctx
from atlas.api.errors import PROBLEM_RESPONSES
from atlas.api.rbac import OPS, REVENUE, VIEW

analytics = APIRouter(prefix="/analytics", tags=["analytics"], responses=PROBLEM_RESPONSES)
platform = APIRouter(prefix="/platform", tags=["platform"], responses=PROBLEM_RESPONSES)
DEMO = {"demo_data": True, "label": "Demo data: synthetic tenants, subscriptions, and invoices"}


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


# --- Platform revenue (synthetic) --------------------------------------------------------------


@platform.get("/revenue/summary", summary="Current MRR, ARR, and customer counts")
def revenue_summary(ctx: Ctx = Authorized(REVENUE)) -> dict[str, Any]:
    latest = ctx.conn.execute(
        "SELECT month_end, active_customers, mrr FROM mrr_monthly ORDER BY month_end DESC LIMIT 1"
    ).fetchone()
    previous = ctx.conn.execute(
        "SELECT mrr FROM mrr_monthly ORDER BY month_end DESC OFFSET 1 LIMIT 1"
    ).fetchone()
    mrr = float(latest["mrr"]) if latest else 0.0
    prev = float(previous["mrr"]) if previous else 0.0
    return {
        **DEMO,
        "as_of": latest["month_end"] if latest else None,
        "mrr": mrr,
        "arr": round(mrr * 12, 2),
        "active_customers": latest["active_customers"] if latest else 0,
        "mrr_growth_pct": round(100 * (mrr - prev) / prev, 2) if prev else None,
    }


@platform.get("/revenue/mrr", summary="Monthly recurring revenue series")
def revenue_mrr(ctx: Ctx = Authorized(REVENUE)) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT month_end, active_customers, mrr FROM mrr_monthly ORDER BY month_end"
    ).fetchall()
    return {**DEMO, "items": rows}


@platform.get("/revenue/cohorts", summary="Revenue retention by signup cohort")
def revenue_cohorts(ctx: Ctx = Authorized(REVENUE)) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT cohort, months_since, mrr, retention FROM cohort_retention "
        "ORDER BY cohort, months_since"
    ).fetchall()
    return {**DEMO, "items": rows}


@platform.get("/revenue/tenants", summary="Revenue by tenant")
def revenue_tenants(ctx: Ctx = Authorized(REVENUE)) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT t.id, t.name, t.industry, t.region, t.acquisition_channel, t.churned_at, "
        "coalesce(sum(CASE WHEN s.billing_cycle = 'annual' THEN s.price / 12 ELSE s.price END), 0) "
        "AS mrr FROM tenants t LEFT JOIN subscriptions s ON s.tenant_id = t.id "
        "AND s.start_date <= current_date AND (s.end_date IS NULL OR s.end_date > current_date) "
        "GROUP BY t.id ORDER BY mrr DESC, t.name LIMIT 200"
    ).fetchall()
    return {**DEMO, "items": rows}
