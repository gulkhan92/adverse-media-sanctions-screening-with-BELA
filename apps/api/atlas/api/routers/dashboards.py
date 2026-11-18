"""Dashboard endpoints: platform revenue (synthetic demo data) and operational analytics.

Common query parameters: ``range`` (30d, 90d, 12m, all, custom), ``from`` and ``to`` for custom
ranges, ``granularity``, ``compare`` (previous period), and the filters ``plan``, ``region``,
``industry``, ``channel``. Add ``format=csv`` to export tabular results.
Responses are cached for a short time per tenant scope.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from atlas.api import ops_analytics as ops
from atlas.api import revenue as rev
from atlas.api.cache import ResponseCache
from atlas.api.deps import Authorized, AuthorizedPlatform, Ctx
from atlas.api.errors import PROBLEM_RESPONSES, not_found
from atlas.api.export import csv_response
from atlas.api.rbac import OPS, REVENUE

revenue = APIRouter(prefix="/platform/revenue", tags=["platform"], responses=PROBLEM_RESPONSES)
analytics = APIRouter(prefix="/analytics", tags=["analytics"], responses=PROBLEM_RESPONSES)
DEMO = {"demo_data": True, "label": "Demo data: synthetic tenants, subscriptions, and invoices"}
OPS_DEMO = {
    "demo_data": True,
    "label": "Demo data: includes synthetic usage and alert history through 2026-10-31",
}


@dataclass
class Query_:
    range: str
    from_: dt.date | None
    to: dt.date | None
    granularity: str
    compare: bool
    filters: rev.Filters
    fmt: str

    def key(self) -> tuple[Any, ...]:
        return (
            self.range,
            self.from_,
            self.to,
            self.granularity,
            self.compare,
            self.filters.key(),
            self.fmt,
        )


def common(
    range: str = Query(default="12m", pattern="^(30d|90d|12m|all|custom)$"),
    from_: dt.date | None = Query(default=None, alias="from"),
    to: dt.date | None = None,
    granularity: str = Query(default="month", pattern="^(day|week|month)$"),
    compare: bool = False,
    plan: str | None = Query(default=None, pattern="^(starter|professional|enterprise)$"),
    region: str | None = Query(default=None, max_length=40),
    industry: str | None = Query(default=None, max_length=40),
    channel: str | None = Query(default=None, max_length=40),
    format: str = Query(default="json", pattern="^(json|csv)$"),
) -> Query_:
    return Query_(
        range, from_, to, granularity, compare, rev.Filters(plan, region, industry, channel), format
    )


def cache_of(ctx: Ctx) -> ResponseCache:
    cache: ResponseCache = ctx.state.extras["cache"]
    return cache


def serve(
    ctx: Ctx,
    response: Response,
    name: str,
    q: Query_,
    compute: Callable[[], dict[str, Any]],
    rows: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
    extra: tuple[Any, ...] = (),
) -> Any:
    """Return the cached result or compute it; export CSV when requested."""
    key = (name, ctx.scope.tenant_id, ctx.scope.platform, q.key(), extra)
    cache = cache_of(ctx)
    payload = cache.get(key)
    response.headers["X-Cache"] = "hit" if payload is not None else "miss"
    if payload is None:
        payload = compute()
        cache.set(key, payload)
    if q.fmt == "csv":
        if rows is None:
            from atlas.api.errors import bad_request

            raise bad_request("This endpoint has no tabular export.", "not-exportable")
        ctx.audit("analytics.exported", name)
        return csv_response(rows(payload), name.replace("/", "-"))
    return payload


def revenue_envelope(ctx: Ctx, as_of: dt.date | None) -> dict[str, Any]:
    return {**DEMO, "as_of": as_of, "refreshed_at": rev.refreshed_at(ctx.conn)}


# --- Platform revenue -----------------------------------------------------------------------------


def revenue_window(ctx: Ctx, q: Query_) -> rev.Window:
    return rev.resolve_window(ctx.conn, q.range, q.from_, q.to, q.granularity, q.compare)


@revenue.get("/summary", summary="Current MRR, ARR, and customer counts")
def summary(ctx: Ctx = AuthorizedPlatform(REVENUE)) -> dict[str, Any]:
    series = rev.monthly_series(ctx.conn, rev.Filters())
    months = sorted(series)
    if not months:
        return {
            **DEMO,
            "as_of": None,
            "mrr": 0.0,
            "arr": 0.0,
            "active_customers": 0,
            "mrr_growth_pct": None,
        }
    last = series[months[-1]]
    prev = series[months[-2]]["mrr"] if len(months) > 1 else 0.0
    return {
        **revenue_envelope(ctx, months[-1]),
        "mrr": last["mrr"],
        "arr": last["arr"],
        "active_customers": last["active_customers"],
        "mrr_growth_pct": round(100 * (last["mrr"] - prev) / prev, 2) if prev else None,
    }


@revenue.get("/kpis", summary="KPI cards with change versus the previous period and sparklines")
def kpis(
    response: Response, q: Query_ = Depends(common), ctx: Ctx = AuthorizedPlatform(REVENUE)
) -> Any:
    def compute() -> dict[str, Any]:
        window = revenue_window(ctx, q)
        series = rev.monthly_series(ctx.conn, q.filters)
        notes = []
        if q.filters.tenant_only:
            notes.append("CAC metrics are not available with plan, region, or industry filters.")
        return {
            **revenue_envelope(ctx, window.as_of),
            **rev.kpi_report(series, window),
            "window": window.describe(),
            "assumptions": {
                "gross_margin": rev.GROSS_MARGIN,
                "note": "Gross margin is a fixed 80 percent assumption.",
            },
            "notes": notes,
        }

    return serve(
        ctx,
        response,
        "revenue/kpis",
        q,
        compute,
        lambda p: [
            {"kpi": name, "month_end": point["month_end"], "value": point["value"]}
            for name, k in p["kpis"].items()
            for point in k["sparkline"]
        ],
    )


@revenue.get("/mrr", summary="MRR over time, stacked by plan, with event annotations")
def mrr(
    response: Response, q: Query_ = Depends(common), ctx: Ctx = AuthorizedPlatform(REVENUE)
) -> Any:
    def compute() -> dict[str, Any]:
        window = revenue_window(ctx, q)
        series = rev.monthly_series(ctx.conn, q.filters)
        by_plan = rev.mrr_by_plan(ctx.conn, q.filters, window.months)
        events = ctx.conn.execute(
            "SELECT day, label FROM business_events WHERE day BETWEEN %s AND %s ORDER BY day",
            (window.months[0].replace(day=1), window.months[-1]) if window.months else (None, None),
        ).fetchall()
        return {
            **revenue_envelope(ctx, window.as_of),
            "window": window.describe(),
            "items": [
                {
                    "month_end": m,
                    "active_customers": series[m]["active_customers"],
                    "mrr": series[m]["mrr"],
                    "by_plan": by_plan[m],
                }
                for m in window.months
            ],
            "events": events,
        }

    return serve(
        ctx,
        response,
        "revenue/mrr",
        q,
        compute,
        lambda p: [
            {
                "month_end": i["month_end"],
                "mrr": i["mrr"],
                "active_customers": i["active_customers"],
                **{f"mrr_{k}": v for k, v in i["by_plan"].items()},
            }
            for i in p["items"]
        ],
    )


@revenue.get("/movements", summary="MRR movements: new, expansion, contraction, churn")
def movements(
    response: Response, q: Query_ = Depends(common), ctx: Ctx = AuthorizedPlatform(REVENUE)
) -> Any:
    def compute() -> dict[str, Any]:
        window = revenue_window(ctx, q)
        series = rev.monthly_series(ctx.conn, q.filters)
        items = []
        for m in window.months:
            s = series[m]
            items.append(
                {
                    "month_end": m,
                    "begin_mrr": s["begin_mrr"],
                    "new": s["new_mrr"],
                    "expansion": s["expansion_mrr"],
                    "contraction": -s["contraction_mrr"],
                    "churn": -s["churn_mrr"],
                    "net_new": s["net_new_mrr"],
                    "end_mrr": s["mrr"],
                    "new_customers": s["new_customers"],
                    "churned_customers": s["churned_customers"],
                }
            )
        return {
            **revenue_envelope(ctx, window.as_of),
            "window": window.describe(),
            "note": "Contraction and churn are negative. new + expansion + contraction + "
            "churn = end_mrr - begin_mrr for every month.",
            "items": items,
        }

    return serve(ctx, response, "revenue/movements", q, compute, lambda p: p["items"])


@revenue.get("/mix", summary="Subscription versus usage overage revenue")
def mix(
    response: Response, q: Query_ = Depends(common), ctx: Ctx = AuthorizedPlatform(REVENUE)
) -> Any:
    def compute() -> dict[str, Any]:
        window = revenue_window(ctx, q)
        return {
            **revenue_envelope(ctx, window.as_of),
            "window": window.describe(),
            "note": "Billed amounts by invoice period. Overage is usage revenue and is not "
            "part of MRR.",
            "items": rev.revenue_mix(ctx.conn, window.months),
        }

    return serve(ctx, response, "revenue/mix", q, compute, lambda p: p["items"])


@revenue.get("/forecast", summary="Six month MRR projection with an 80 percent interval")
def forecast(
    response: Response, q: Query_ = Depends(common), ctx: Ctx = AuthorizedPlatform(REVENUE)
) -> Any:
    def compute() -> dict[str, Any]:
        series = rev.monthly_series(ctx.conn, rev.Filters())
        history = [(m, series[m]["mrr"]) for m in sorted(series)]
        result = rev.forecast(history)
        return {**revenue_envelope(ctx, history[-1][0] if history else None), **result}

    return serve(ctx, response, "revenue/forecast", q, compute, lambda p: p["projection"])


@revenue.get("/cohorts", summary="Revenue retention by signup cohort")
def cohorts(
    response: Response, q: Query_ = Depends(common), ctx: Ctx = AuthorizedPlatform(REVENUE)
) -> Any:
    def compute() -> dict[str, Any]:
        rows = ctx.conn.execute(
            "SELECT cohort, months_since, mrr, retention FROM cohort_retention "
            "ORDER BY cohort, months_since"
        ).fetchall()
        return {
            **revenue_envelope(ctx, None),
            "items": [
                {
                    "cohort": r["cohort"],
                    "months_since": r["months_since"],
                    "mrr": rev.num(r["mrr"]),
                    "retention": rev.num(r["retention"]),
                    "retention_pct": None
                    if r["retention"] is None
                    else round(100 * float(r["retention"]), 1),
                }
                for r in rows
            ],
        }

    return serve(ctx, response, "revenue/cohorts", q, compute, lambda p: p["items"])


@revenue.get("/dimensions", summary="MRR by plan, region, industry, or acquisition channel")
def dimensions(
    response: Response,
    by: str = Query(default="region", pattern="^(plan|region|industry|channel)$"),
    q: Query_ = Depends(common),
    ctx: Ctx = AuthorizedPlatform(REVENUE),
) -> Any:
    def compute() -> dict[str, Any]:
        window = revenue_window(ctx, q)
        return {
            **revenue_envelope(ctx, window.as_of),
            "window": window.describe(),
            **rev.dimension_breakdown(ctx.conn, by, window.months),
        }

    return serve(
        ctx, response, f"revenue/dimensions-{by}", q, compute, lambda p: p["items"], extra=(by,)
    )


@revenue.get("/concentration", summary="Top customers by share of MRR")
def concentration(
    response: Response, q: Query_ = Depends(common), ctx: Ctx = AuthorizedPlatform(REVENUE)
) -> Any:
    def compute() -> dict[str, Any]:
        window = revenue_window(ctx, q)
        return {
            **revenue_envelope(ctx, window.as_of),
            **rev.concentration(ctx.conn, window.months[-1]),
        }

    return serve(ctx, response, "revenue/concentration", q, compute, lambda p: p["items"])


@revenue.get("/unit-economics", summary="CAC, LTV, LTV to CAC, and payback")
def unit_economics(
    response: Response, q: Query_ = Depends(common), ctx: Ctx = AuthorizedPlatform(REVENUE)
) -> Any:
    def compute() -> dict[str, Any]:
        window = revenue_window(ctx, q)
        series = rev.monthly_series(ctx.conn, q.filters)
        return {
            **revenue_envelope(ctx, window.as_of),
            "window": window.describe(),
            **rev.unit_economics(series, window.months, ctx.conn),
            "notes": [
                "CAC payback and LTV use the trailing three month CAC and a fixed 80 "
                "percent gross margin; LTV uses the trailing six month logo churn."
            ],
        }

    return serve(ctx, response, "revenue/unit-economics", q, compute, lambda p: p["items"])


@revenue.get("/billing-health", summary="Open and overdue invoices with aging")
def billing_health(
    response: Response, q: Query_ = Depends(common), ctx: Ctx = AuthorizedPlatform(REVENUE)
) -> Any:
    def compute() -> dict[str, Any]:
        months = rev.month_ends(ctx.conn)
        if not months:
            return {**revenue_envelope(ctx, None), "status": [], "aging": [], "items": []}
        return {**revenue_envelope(ctx, months[-1]), **rev.billing_health(ctx.conn, months[-1])}

    return serve(ctx, response, "revenue/billing-health", q, compute, lambda p: p["items"])


@revenue.get("/customers", summary="Customer table with usage versus quota and sparklines")
def customers(
    response: Response,
    q: Query_ = Depends(common),
    search: str | None = Query(default=None, max_length=100),
    sort: str = Query(default="mrr", pattern="^(mrr|name|usage|last_activity|plan|region)$"),
    descending: bool = True,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: Ctx = AuthorizedPlatform(REVENUE),
) -> Any:
    def compute() -> dict[str, Any]:
        as_of = rev.month_ends(ctx.conn)[-1]
        return {
            **revenue_envelope(ctx, as_of),
            **rev.customer_table(
                ctx.conn, q.filters, as_of, search, sort, descending, limit, offset
            ),
        }

    return serve(
        ctx,
        response,
        "revenue/customers",
        q,
        compute,
        lambda p: [{k: v for k, v in i.items() if k != "mrr_history"} for i in p["items"]],
        extra=(search, sort, descending, limit, offset),
    )


@revenue.get("/customers/{tenant_id}", summary="Customer drill-down")
def customer(
    tenant_id: uuid.UUID, response: Response, ctx: Ctx = AuthorizedPlatform(REVENUE)
) -> dict[str, Any]:
    months = rev.month_ends(ctx.conn)
    if not months:
        raise not_found("Customer")
    as_of = months[-1]
    detail = rev.customer_detail(ctx.conn, tenant_id, as_of)
    if detail is None:
        raise not_found("Customer")
    return {**revenue_envelope(ctx, as_of), **detail}


@revenue.post("/refresh", summary="Refresh the business views now")
def refresh(ctx: Ctx = AuthorizedPlatform(REVENUE)) -> dict[str, Any]:
    row = ctx.conn.execute("SELECT refresh_business_views() AS refreshed_at").fetchone()
    cache_of(ctx).clear()  # the refresh itself commits with this request
    ctx.audit("analytics.views_refreshed", "business")
    return {"refreshed_at": row["refreshed_at"] if row else None}


# --- Operational analytics -------------------------------------------------------------


def ops_window(ctx: Ctx, q: Query_) -> ops.OpsWindow:
    return ops.resolve_window(ctx.conn, q.range, q.from_, q.to, q.granularity, q.compare)


def ops_envelope(w: ops.OpsWindow) -> dict[str, Any]:
    return {**OPS_DEMO, "as_of": w.as_of, "window": w.describe()}


@analytics.get("/kpis", summary="Operational KPI cards")
def ops_kpis(response: Response, q: Query_ = Depends(common), ctx: Ctx = Authorized(OPS)) -> Any:
    def compute() -> dict[str, Any]:
        w = ops_window(ctx, q)
        return {**ops_envelope(w), "kpis": ops.kpis(ctx.conn, w, ctx.scope.platform)}

    return serve(
        ctx,
        response,
        "analytics/kpis",
        q,
        compute,
        lambda p: [{"kpi": k, **v} for k, v in p["kpis"].items() if isinstance(v, dict)],
    )


def _ops_series(name: str, fetch: Callable[[Ctx, ops.OpsWindow], Any]) -> Callable[..., Any]:
    def handler(response: Response, q: Query_ = Depends(common), ctx: Ctx = Authorized(OPS)) -> Any:
        def compute() -> dict[str, Any]:
            w = ops_window(ctx, q)
            return {**ops_envelope(w), "items": fetch(ctx, w)}

        return serve(ctx, response, f"analytics/{name}", q, compute, lambda p: p["items"])

    return handler


analytics.get("/volume", summary="Screening volume over time")(
    _ops_series("volume", lambda c, w: ops.volume(c.conn, w))
)
analytics.get("/severity", summary="Alerts by severity over time")(
    _ops_series("severity", lambda c, w: ops.severity(c.conn, w))
)
analytics.get("/latency", summary="Screening latency (p50 and p95)")(
    _ops_series("latency", lambda c, w: ops.latency(c.conn, w))
)
analytics.get("/languages", summary="Mentions by article language")(
    _ops_series("languages", lambda c, w: ops.language_mix(c.conn, w))
)
analytics.get("/categories", summary="Adverse media findings by category and severity")(
    _ops_series("categories", lambda c, w: ops.category_mix(c.conn, w))
)
analytics.get("/risk-by-country", summary="Weighted alert count by subject country")(
    _ops_series("risk-by-country", lambda c, w: ops.risk_by_country(c.conn, w))
)
analytics.get("/throughput", summary="Alerts resolved per analyst per week")(
    _ops_series("throughput", lambda c, w: ops.throughput(c.conn, w))
)


@analytics.get("/quality", summary="Precision and false positive trend with version markers")
def quality(response: Response, q: Query_ = Depends(common), ctx: Ctx = Authorized(OPS)) -> Any:
    def compute() -> dict[str, Any]:
        w = ops_window(ctx, q)
        return {
            **ops_envelope(w),
            **ops.quality_series(ctx.conn, w, ctx.scope.platform),
            "definition": "precision = confirmed true matches / (true matches + false positives)",
        }

    return serve(ctx, response, "analytics/quality", q, compute, lambda p: p["items"])


@analytics.get("/queue-ageing", summary="Open alerts by age with the SLA threshold")
def queue_ageing(
    response: Response, q: Query_ = Depends(common), ctx: Ctx = Authorized(OPS)
) -> Any:
    def compute() -> dict[str, Any]:
        return {
            **OPS_DEMO,
            "demo_data": False,
            "label": "Live data",
            **ops.queue_ageing(ctx.conn, ctx.state.settings.queue_sla_hours),
        }

    return serve(ctx, response, "analytics/queue-ageing", q, compute, lambda p: p["buckets"])


@analytics.get("/routing", summary="Mention routing: BELA, BLINK, LLM, NIL")
def routing(response: Response, q: Query_ = Depends(common), ctx: Ctx = Authorized(OPS)) -> Any:
    return serve(
        ctx,
        response,
        "analytics/routing",
        q,
        lambda: {"demo_data": False, **ops.routing(ctx.conn)},
        lambda p: p["links"],
    )


@analytics.get("/hybrid-vs-baseline", summary="False matches: fuzzy baseline, BELA, and hybrid")
def hybrid_vs_baseline(ctx: Ctx = Authorized(OPS)) -> dict[str, Any]:
    return ops.hybrid_vs_baseline()
