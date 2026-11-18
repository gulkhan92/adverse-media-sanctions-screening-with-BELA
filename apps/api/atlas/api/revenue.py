"""Revenue analytics (PLAN.md phase 6). Metric definitions are documented in docs/metrics.md.

All figures come from the materialized views created in migration 0006, which are built from the
synthetic business tables. Revenue metrics are monthly snapshots, so the granularity is month.
Every response is labeled as demo data by the router.
"""

from __future__ import annotations

import datetime as dt
import math
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
import psycopg
from statsmodels.tsa.exponential_smoothing.ets import ETSModel  # imported eagerly: ~0.6 s

from atlas.api.db import Row
from atlas.api.errors import bad_request

GROSS_MARGIN = 0.80  # fixed assumption, shown wherever payback and LTV appear
FORECAST_MONTHS = 6
FORECAST_ALPHA = 0.20  # 80 percent interval
MIN_FORECAST_HISTORY = 24
RANGES = {"30d": 1, "90d": 3, "12m": 12}
KPI_ORDER = [
    "mrr",
    "arr",
    "net_new_mrr",
    "nrr",
    "grr",
    "logo_churn",
    "arpa",
    "active_customers",
    "quick_ratio",
    "cac_payback_months",
]


def num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


@dataclass(frozen=True)
class Filters:
    plan: str | None = None
    region: str | None = None
    industry: str | None = None
    channel: str | None = None

    @property
    def active(self) -> bool:
        return any((self.plan, self.region, self.industry, self.channel))

    @property
    def tenant_only(self) -> bool:
        """Filters that cannot be applied to marketing spend (it has no tenant dimension)."""
        return any((self.plan, self.region, self.industry))

    def sql(self, alias: str = "") -> tuple[str, list[Any]]:
        prefix = f"{alias}." if alias else ""
        parts, params = [], []
        for column, value in (
            ("plan_code", self.plan),
            ("region", self.region),
            ("industry", self.industry),
            ("channel", self.channel),
        ):
            if value:
                parts.append(f"{prefix}{column} = %s")
                params.append(value)
        return (" AND " + " AND ".join(parts)) if parts else "", params

    def key(self) -> tuple[str | None, ...]:
        return (self.plan, self.region, self.industry, self.channel)


@dataclass
class Window:
    months: list[dt.date]
    compare: list[dt.date] = field(default_factory=list)
    as_of: dt.date | None = None

    def describe(self) -> dict[str, Any]:
        if not self.months:
            return {"from": None, "to": None, "months": 0, "compare_from": None, "compare_to": None}
        return {
            "from": self.months[0],
            "to": self.months[-1],
            "months": len(self.months),
            "compare_from": self.compare[0] if self.compare else None,
            "compare_to": self.compare[-1] if self.compare else None,
        }


def month_ends(conn: psycopg.Connection[Row]) -> list[dt.date]:
    rows = conn.execute("SELECT DISTINCT month_end FROM tenant_mrr_monthly ORDER BY 1").fetchall()
    return [r["month_end"] for r in rows]


def resolve_window(
    conn: psycopg.Connection[Row],
    range_: str,
    start: dt.date | None,
    end: dt.date | None,
    granularity: str,
    compare: bool,
) -> Window:
    if granularity != "month":
        raise bad_request(
            "Revenue metrics are monthly snapshots; use granularity=month.",
            "unsupported-granularity",
        )
    all_months = month_ends(conn)
    if not all_months:
        return Window([], [], None)
    as_of = all_months[-1]
    if range_ == "all":
        chosen = list(all_months)
    elif range_ in RANGES:
        chosen = all_months[-RANGES[range_] :]
    elif range_ == "custom":
        if start is None or end is None or start > end:
            raise bad_request(
                "Custom ranges need from and to, with from not after to.", "invalid-range"
            )
        chosen = [m for m in all_months if start <= m <= end] or []
        if not chosen:
            raise bad_request("The range contains no complete month.", "invalid-range")
    else:
        raise bad_request("range must be 30d, 90d, 12m, all, or custom.", "invalid-range")
    previous: list[dt.date] = []
    if compare:
        first = all_months.index(chosen[0])
        previous = all_months[max(0, first - len(chosen)) : first]
    return Window(chosen, previous, as_of)


# --- Monthly KPI series ------------------------------------------------------------------------


def monthly_series(conn: psycopg.Connection[Row], f: Filters) -> dict[dt.date, dict[str, Any]]:
    """All KPIs for every month. Filters apply to tenant attributes at that month."""
    where, params = f.sql()
    base = conn.execute(
        "SELECT month_end, sum(mrr) AS mrr, count(*) FILTER (WHERE mrr > 0) AS customers "
        f"FROM tenant_mrr_monthly WHERE true{where} GROUP BY month_end ORDER BY month_end",  # nosec B608
        params,
    ).fetchall()
    moves = {
        r["month_end"]: r
        for r in conn.execute(
            "SELECT month_end, sum(prev_mrr) AS begin_mrr, "
            "coalesce(sum(delta) FILTER (WHERE movement = 'new'), 0) AS new_mrr, "
            "coalesce(sum(delta) FILTER (WHERE movement = 'expansion'), 0) AS expansion_mrr, "
            "coalesce(-sum(delta) FILTER (WHERE movement = 'contraction'), 0) AS contraction_mrr, "
            "coalesce(-sum(delta) FILTER (WHERE movement = 'churn'), 0) AS churn_mrr, "
            "count(*) FILTER (WHERE movement = 'new') AS new_customers, "
            "count(*) FILTER (WHERE movement = 'churn') AS churned_customers, "
            "count(*) FILTER (WHERE prev_mrr > 0) AS begin_customers "
            f"FROM tenant_mrr_movements WHERE true{where} GROUP BY month_end",  # nosec B608
            params,
        )
    }
    retention = {
        r["month_end"]: r
        for r in conn.execute(
            "SELECT cur.month_end, sum(cur.mrr) / nullif(sum(prev.mrr), 0) AS nrr, "
            "sum(least(cur.mrr, prev.mrr)) / nullif(sum(prev.mrr), 0) AS grr "
            "FROM tenant_mrr_monthly prev JOIN tenant_mrr_monthly cur "
            "ON cur.tenant_id = prev.tenant_id AND cur.month_end = "
            "(date_trunc('month', prev.month_end) + interval '13 months - 1 day')::date "
            f"WHERE prev.mrr > 0{f.sql('prev')[0]} GROUP BY cur.month_end",  # nosec B608
            f.sql("prev")[1],
        )
    }
    spend = {
        r["month"]: num(r["spend"])
        for r in conn.execute(
            "SELECT month, sum(spend) AS spend FROM marketing_spend "
            "WHERE (%s::text IS NULL OR channel = %s) GROUP BY month",
            (f.channel, f.channel),
        )
    }
    out: dict[dt.date, dict[str, Any]] = {}
    history: list[dict[str, Any]] = []
    for r in base:
        m = r["month_end"]
        mv = moves.get(m, {})
        mrr = num(r["mrr"]) or 0.0
        customers = int(r["customers"])
        new_c = int(mv.get("new_customers", 0) or 0)
        churned = int(mv.get("churned_customers", 0) or 0)
        begin_c = int(mv.get("begin_customers", 0) or 0)
        up = (num(mv.get("new_mrr")) or 0.0) + (num(mv.get("expansion_mrr")) or 0.0)
        down = (num(mv.get("contraction_mrr")) or 0.0) + (num(mv.get("churn_mrr")) or 0.0)
        spend_m = spend.get(m.replace(day=1)) if not f.tenant_only else None
        row: dict[str, Any] = {
            "month_end": m,
            "mrr": mrr,
            "arr": mrr * 12,
            "active_customers": customers,
            "net_new_mrr": up - down,
            "new_mrr": num(mv.get("new_mrr")) or 0.0,
            "expansion_mrr": num(mv.get("expansion_mrr")) or 0.0,
            "contraction_mrr": num(mv.get("contraction_mrr")) or 0.0,
            "churn_mrr": num(mv.get("churn_mrr")) or 0.0,
            "begin_mrr": num(mv.get("begin_mrr")) or 0.0,
            "new_customers": new_c,
            "churned_customers": churned,
            "nrr": num((retention.get(m) or {}).get("nrr")),
            "grr": num((retention.get(m) or {}).get("grr")),
            "logo_churn": (churned / begin_c) if begin_c else None,
            "arpa": (mrr / customers) if customers else None,
            "quick_ratio": (up / down) if down else None,
            "marketing_spend": spend_m,
            "cac": (spend_m / new_c) if spend_m is not None and new_c else None,
        }
        history.append(row)
        window3 = history[-3:]
        new3 = sum(h["new_customers"] for h in window3)
        spend3 = [h["marketing_spend"] for h in window3]
        row["cac_t3"] = (
            sum(s for s in spend3 if s is not None) / new3
            if new3 and all(s is not None for s in spend3)
            else None
        )
        churn6 = [h["logo_churn"] for h in history[-6:] if h["logo_churn"] is not None]
        row["monthly_churn_t6"] = (sum(churn6) / len(churn6)) if churn6 else None
        arpa = row["arpa"]
        row["cac_payback_months"] = (
            row["cac_t3"] / (arpa * GROSS_MARGIN) if row["cac_t3"] and arpa else None
        )
        row["ltv"] = (
            arpa * GROSS_MARGIN / row["monthly_churn_t6"]
            if arpa and row["monthly_churn_t6"]
            else None
        )
        row["ltv_cac"] = row["ltv"] / row["cac_t3"] if row["ltv"] and row["cac_t3"] else None
        out[m] = row
    return out


def kpi_report(series: dict[dt.date, dict[str, Any]], window: Window) -> dict[str, Any]:
    if not window.months:
        return {"kpis": {}}
    last = window.months[-1]
    prev_last = window.compare[-1] if window.compare else None
    kpis: dict[str, Any] = {}
    for name in KPI_ORDER:
        value = series.get(last, {}).get(name)
        previous = series.get(prev_last, {}).get(name) if prev_last else None
        change = (value - previous) if value is not None and previous is not None else None
        pct = (change / abs(previous) * 100) if change is not None and previous else None
        kpis[name] = {
            "value": value,
            "previous": previous,
            "change": change,
            "change_pct": None if pct is None else round(pct, 2),
            "sparkline": [
                {"month_end": m, "value": series.get(m, {}).get(name)} for m in window.months
            ],
        }
    return {"kpis": kpis}


# --- Stacked series and breakdowns -------------------------------------------------------------


def mrr_by_plan(
    conn: psycopg.Connection[Row], f: Filters, months: list[dt.date]
) -> dict[dt.date, dict[str, float]]:
    where, params = f.sql()
    rows = conn.execute(
        "SELECT month_end, plan_code, sum(mrr) AS mrr FROM tenant_mrr_monthly "
        f"WHERE month_end = ANY(%s){where} GROUP BY 1, 2",  # nosec B608
        [months, *params],
    ).fetchall()
    out: dict[dt.date, dict[str, float]] = {m: {} for m in months}
    for r in rows:
        out[r["month_end"]][r["plan_code"]] = num(r["mrr"]) or 0.0
    return out


def revenue_mix(conn: psycopg.Connection[Row], months: list[dt.date]) -> list[Row]:
    firsts = [m.replace(day=1) for m in months]
    rows = conn.execute(
        "SELECT month, subscription, overage, credit, total FROM usage_revenue_monthly "
        "WHERE month = ANY(%s) ORDER BY month",
        (firsts,),
    ).fetchall()
    return [
        {
            "month": r["month"],
            "subscription": num(r["subscription"]),
            "overage": num(r["overage"]),
            "credit": num(r["credit"]),
            "total": num(r["total"]),
            "overage_share_pct": round(100 * float(r["overage"]) / float(r["total"]), 2)
            if r["total"]
            else None,
        }
        for r in rows
    ]


def dimension_breakdown(
    conn: psycopg.Connection[Row], by: str, months: list[dt.date]
) -> dict[str, Any]:
    if by not in ("plan", "region", "industry", "channel"):
        raise bad_request("by must be plan, region, industry, or channel.", "invalid-dimension")
    rows = conn.execute(
        "SELECT month_end, value, mrr, customers FROM revenue_by_dimension "
        "WHERE dimension = %s AND month_end = ANY(%s) ORDER BY month_end, value",
        (by, months),
    ).fetchall()
    latest = months[-1] if months else None
    current = [r for r in rows if r["month_end"] == latest]
    total = sum(float(r["mrr"]) for r in current) or 1.0
    ranked = sorted(
        (
            {
                "value": r["value"],
                "mrr": num(r["mrr"]),
                "arr": (num(r["mrr"]) or 0) * 12,
                "customers": r["customers"],
                "share_pct": round(100 * float(r["mrr"]) / total, 2),
            }
            for r in current
        ),
        key=lambda x: x["mrr"] or 0,
        reverse=True,
    )
    series = [
        {"month_end": r["month_end"], "value": r["value"], "mrr": num(r["mrr"])} for r in rows
    ]
    return {"dimension": by, "as_of": latest, "items": ranked, "series": series}


def concentration(conn: psycopg.Connection[Row], month: dt.date, top: int = 10) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT m.tenant_id, t.name, m.mrr, m.plan_code FROM tenant_mrr_monthly m "
        "JOIN tenants t ON t.id = m.tenant_id WHERE m.month_end = %s AND m.mrr > 0 "
        "ORDER BY m.mrr DESC, t.name",
        (month,),
    ).fetchall()
    total = sum(float(r["mrr"]) for r in rows) or 1.0
    items, running = [], 0.0
    for rank, r in enumerate(rows[:top], start=1):
        running += float(r["mrr"])
        items.append(
            {
                "rank": rank,
                "tenant_id": r["tenant_id"],
                "name": r["name"],
                "plan": r["plan_code"],
                "mrr": num(r["mrr"]),
                "share_pct": round(100 * float(r["mrr"]) / total, 2),
                "cumulative_share_pct": round(100 * running / total, 2),
            }
        )
    return {
        "as_of": month,
        "total_mrr": total,
        "customers": len(rows),
        "items": items,
        "top_share_pct": items[-1]["cumulative_share_pct"] if items else None,
    }


def unit_economics(
    series: dict[dt.date, dict[str, Any]], months: list[dt.date], conn: psycopg.Connection[Row]
) -> dict[str, Any]:
    items = [
        {
            k: series[m].get(k)
            for k in (
                "month_end",
                "cac",
                "cac_t3",
                "ltv",
                "ltv_cac",
                "cac_payback_months",
                "arpa",
                "monthly_churn_t6",
                "new_customers",
                "marketing_spend",
            )
        }
        for m in months
        if m in series
    ]
    first, last = months[0].replace(day=1), months[-1].replace(day=1)
    by_channel = conn.execute(
        "SELECT s.channel, s.spend, coalesce(n.new_customers, 0) AS new_customers FROM "
        "(SELECT channel, sum(spend) AS spend FROM marketing_spend "
        " WHERE month BETWEEN %s AND %s GROUP BY channel) s "
        "LEFT JOIN (SELECT channel, count(*) AS new_customers FROM tenant_mrr_movements "
        " WHERE movement = 'new' AND month_end BETWEEN %s AND %s GROUP BY channel) n "
        "ON n.channel = s.channel ORDER BY s.channel",
        (first, last, months[0], months[-1]),
    ).fetchall()
    channels = [
        {
            "channel": r["channel"],
            "spend": num(r["spend"]),
            "new_customers": r["new_customers"],
            "cac": (float(r["spend"]) / r["new_customers"]) if r["new_customers"] else None,
        }
        for r in by_channel
    ]
    return {"gross_margin": GROSS_MARGIN, "items": items, "by_channel": channels}


# --- Forecast ----------------------------------------------------------------------------------


def _fit(y: pd.Series[float], steps: int) -> pd.DataFrame:
    model = ETSModel(y, error="add", trend="add", damped_trend=True).fit(disp=False, maxiter=200)
    frame = model.get_prediction(start=len(y), end=len(y) + steps - 1).summary_frame(
        alpha=FORECAST_ALPHA
    )
    frame.attrs["aic"] = float(model.aic)
    return frame


def forecast(history: list[tuple[dt.date, float]]) -> dict[str, Any]:
    """ETS projection of MRR with an 80 percent interval, plus a holdout backtest."""
    if len(history) < MIN_FORECAST_HISTORY:
        raise bad_request(
            f"At least {MIN_FORECAST_HISTORY} months of history are needed.", "insufficient-history"
        )
    index = pd.date_range(history[0][0], periods=len(history), freq="ME")
    y = pd.Series([v for _, v in history], index=index, dtype=float)
    frame = _fit(y, FORECAST_MONTHS)
    future_index = pd.date_range(
        index[-1] + pd.offsets.MonthEnd(1), periods=FORECAST_MONTHS, freq="ME"
    )
    lower_col, upper_col = frame.columns[-2], frame.columns[-1]
    projection = [
        {
            "month_end": d.date(),
            "mean": float(frame["mean"].iloc[i]),
            "lower": max(0.0, float(frame[lower_col].iloc[i])),
            "upper": float(frame[upper_col].iloc[i]),
        }
        for i, d in enumerate(future_index)
    ]
    # Backtest: refit on all but the last six months and compare with what happened.
    train, test = y.iloc[:-FORECAST_MONTHS], y.iloc[-FORECAST_MONTHS:]
    check = _fit(train, FORECAST_MONTHS)
    predicted = check["mean"].to_numpy()
    mape = float(np.mean(np.abs((test.to_numpy() - predicted) / test.to_numpy())) * 100)
    covered = int(
        np.sum(
            (test.to_numpy() >= check[check.columns[-2]].to_numpy())
            & (test.to_numpy() <= check[check.columns[-1]].to_numpy())
        )
    )
    return {
        "label": "Estimate",
        "method": "ETS (additive error, additive damped trend), statsmodels",
        "interval": "80 percent prediction interval",
        "history_months": len(history),
        "history": [{"month_end": d, "mrr": v} for d, v in history],
        "projection": projection,
        "backtest": {
            "holdout_months": FORECAST_MONTHS,
            "mape_pct": round(mape, 2),
            "months_inside_interval": covered,
        },
        "aic": round(frame.attrs["aic"], 2),
        "disclaimer": "A statistical estimate from synthetic history, not a commitment or advice.",
    }


# --- Billing health and customers ----------------------------------------------------------------


def billing_health(conn: psycopg.Connection[Row], as_of: dt.date) -> dict[str, Any]:
    status = conn.execute(
        "SELECT status, count(*) AS invoices, coalesce(sum(total), 0) AS amount "
        "FROM invoices GROUP BY status ORDER BY status"
    ).fetchall()
    open_rows = conn.execute(
        "SELECT i.id, i.tenant_id, t.name, i.period, i.due_date, i.total, i.status, "
        "greatest(0, %s - (i.period + interval '1 month - 1 day')::date) AS days_outstanding, "
        "greatest(0, %s - i.due_date) AS days_past_due FROM invoices i JOIN tenants t "
        "ON t.id = i.tenant_id WHERE i.status IN ('open', 'overdue') "
        "ORDER BY i.status DESC, days_past_due DESC, i.total DESC LIMIT 200",
        (as_of, as_of),
    ).fetchall()
    buckets = {"not_due": 0.0, "1_30": 0.0, "31_60": 0.0, "61_90": 0.0, "over_90": 0.0}
    counts = dict.fromkeys(buckets, 0)
    for r in open_rows:
        d = r["days_past_due"]
        key = (
            "not_due"
            if d <= 0
            else "1_30"
            if d <= 30
            else "31_60"
            if d <= 60
            else "61_90"
            if d <= 90
            else "over_90"
        )
        buckets[key] += float(r["total"])
        counts[key] += 1
    return {
        "as_of": as_of,
        "status": [
            {"status": r["status"], "invoices": r["invoices"], "amount": num(r["amount"])}
            for r in status
        ],
        "aging": [
            {"bucket": k, "amount": round(v, 2), "invoices": counts[k]} for k, v in buckets.items()
        ],
        "items": [{**r, "total": num(r["total"])} for r in open_rows],
    }


SORTS = {
    "mrr": "mrr",
    "name": "name",
    "usage": "usage_ratio",
    "last_activity": "last_activity",
    "plan": "plan_code",
    "region": "region",
}


def customer_table(
    conn: psycopg.Connection[Row],
    f: Filters,
    as_of: dt.date,
    q: str | None,
    sort: str,
    descending: bool,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    where, params = f.sql("m")
    if q:
        where += " AND t.name ILIKE %s"
        params.append(f"%{q}%")
    order = SORTS.get(sort)
    if order is None:
        raise bad_request(f"sort must be one of {', '.join(SORTS)}.", "invalid-sort")
    direction = "DESC" if descending else "ASC"
    month_first = as_of.replace(day=1)
    rows = conn.execute(
        "SELECT m.tenant_id, t.name, t.region, t.industry, t.churned_at, m.plan_code, m.mrr, "
        "p.included_screenings, "
        "coalesce(u.screenings, 0) AS screenings, "
        "CASE WHEN p.included_screenings > 0 THEN coalesce(u.screenings, 0)::numeric "
        "/ p.included_screenings END AS usage_ratio, u.last_activity, "
        "coalesce(o.overdue, 0) AS overdue_invoices FROM tenant_mrr_monthly m "
        "JOIN tenants t ON t.id = m.tenant_id JOIN plans p ON p.code = m.plan_code "
        "LEFT JOIN LATERAL (SELECT sum(screenings) AS screenings, max(day) AS last_activity "
        " FROM usage_daily WHERE tenant_id = m.tenant_id AND day >= %s AND day <= %s) u ON true "
        "LEFT JOIN LATERAL (SELECT count(*) AS overdue FROM invoices WHERE tenant_id = m.tenant_id "
        " AND status = 'overdue') o ON true "
        f"WHERE m.month_end = %s AND m.mrr > 0{where} "  # nosec B608
        f"ORDER BY {order} {direction} NULLS LAST, t.name LIMIT %s OFFSET %s",  # nosec B608
        [month_first, as_of, as_of, *params, limit, offset],
    ).fetchall()
    total = conn.execute(
        "SELECT count(*) AS n FROM tenant_mrr_monthly m JOIN tenants t ON t.id = m.tenant_id "
        f"WHERE m.month_end = %s AND m.mrr > 0{where}",  # nosec B608
        [as_of, *params],
    ).fetchone()
    ids = [r["tenant_id"] for r in rows]
    spark: dict[uuid.UUID, list[float]] = {i: [] for i in ids}
    for r in conn.execute(
        "SELECT tenant_id, mrr FROM tenant_mrr_monthly WHERE tenant_id = ANY(%s) "
        "AND month_end > %s::date - interval '12 months' AND month_end <= %s ORDER BY month_end",
        (ids, as_of, as_of),
    ):
        spark[r["tenant_id"]].append(num(r["mrr"]) or 0.0)
    items = []
    for r in rows:
        ratio = num(r["usage_ratio"])
        health = (
            "at_risk"
            if r["overdue_invoices"] or (ratio is not None and ratio < 0.25)
            else "healthy"
        )
        items.append(
            {
                "tenant_id": r["tenant_id"],
                "name": r["name"],
                "plan": r["plan_code"],
                "region": r["region"],
                "industry": r["industry"],
                "mrr": num(r["mrr"]),
                "screenings_this_month": int(r["screenings"] or 0),
                "included_screenings": r["included_screenings"],
                "usage_ratio": None if ratio is None else round(ratio, 3),
                "last_activity": r["last_activity"],
                "health": health,
                "overdue_invoices": r["overdue_invoices"],
                "mrr_history": spark[r["tenant_id"]],
            }
        )
    return {"as_of": as_of, "total": total["n"] if total else 0, "items": items}


def customer_detail(
    conn: psycopg.Connection[Row], tenant_id: uuid.UUID, as_of: dt.date
) -> dict[str, Any] | None:
    tenant = conn.execute(
        "SELECT id, name, industry, region, country, acquisition_channel, created_at, churned_at "
        "FROM tenants WHERE id = %s",
        (tenant_id,),
    ).fetchone()
    if tenant is None:
        return None
    subs = conn.execute(
        "SELECT p.code AS plan, s.billing_cycle, s.price, s.start_date, s.end_date, "
        "s.change_reason "
        "FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.tenant_id = %s "
        "ORDER BY s.start_date",
        (tenant_id,),
    ).fetchall()
    invoices = conn.execute(
        "SELECT id, period, status, due_date, total FROM invoices WHERE tenant_id = %s "
        "ORDER BY period DESC LIMIT 24",
        (tenant_id,),
    ).fetchall()
    mrr = conn.execute(
        "SELECT month_end, mrr, plan_code FROM tenant_mrr_monthly WHERE tenant_id = %s "
        "ORDER BY month_end",
        (tenant_id,),
    ).fetchall()
    usage = conn.execute(
        "SELECT date_trunc('month', day)::date AS month, sum(screenings) AS screenings, "
        "sum(batch_rows) AS batch_rows, sum(api_calls) AS api_calls FROM usage_daily "
        "WHERE tenant_id = %s AND day > %s::date - interval '12 months' GROUP BY 1 ORDER BY 1",
        (tenant_id, as_of),
    ).fetchall()
    return {
        "tenant": tenant,
        "subscriptions": [{**s, "price": num(s["price"])} for s in subs],
        "invoices": [{**i, "total": num(i["total"])} for i in invoices],
        "mrr_series": [
            {"month_end": r["month_end"], "mrr": num(r["mrr"]), "plan": r["plan_code"]} for r in mrr
        ],
        "usage_monthly": [
            {
                **u,
                "screenings": int(u["screenings"]),
                "batch_rows": int(u["batch_rows"]),
                "api_calls": int(u["api_calls"]),
            }
            for u in usage
        ],
    }


def refresh_business_views(state: Any) -> dt.datetime:
    """Refresh every business view concurrently (nightly job and on-demand endpoint)."""
    from atlas.api.db import PLATFORM

    with state.db.session(PLATFORM) as conn:
        row = conn.execute("SELECT refresh_business_views() AS refreshed_at").fetchone()
    state.extras["cache"].clear()
    stamp: dt.datetime = row["refreshed_at"]
    return stamp


def refreshed_at(conn: psycopg.Connection[Row]) -> dt.datetime | None:
    row = conn.execute(
        "SELECT refreshed_at FROM view_refresh_log WHERE name = 'business'"
    ).fetchone()
    return row["refreshed_at"] if row else None


def finite(value: float | None) -> float | None:
    return None if value is None or math.isnan(value) or math.isinf(value) else value
