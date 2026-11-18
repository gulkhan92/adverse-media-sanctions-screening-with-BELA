"""Operational analytics (PLAN.md phase 6, task 4). Definitions are in docs/metrics.md.

Queries read ``usage_daily``, ``alert_stats_daily``, and the live alert tables. Tenant users see
their own rows through Row Level Security; platform operators see every tenant. System-wide
series (latency, language mix, adverse categories, routing) contain no tenant data.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from atlas.api.db import Row
from atlas.api.errors import bad_request
from atlas.api.revenue import num

GRANULARITY = ("day", "week", "month")
OPEN_STATUSES = ("new", "in_review", "escalated")
SEVERITY_WEIGHT = {"low": 1, "medium": 2, "high": 3, "critical": 4}
AGE_BUCKETS = [
    ("under_1d", 0, 1),
    ("1_3d", 1, 3),
    ("3_7d", 3, 7),
    ("7_14d", 7, 14),
    ("14_30d", 14, 30),
    ("over_30d", 30, None),
]
EVAL_RESULTS = Path(__file__).resolve().parents[4] / "docs" / "eval" / "results.json"
ALERT_SUM = (
    "coalesce((alerts_by_severity->>'critical')::int, 0) + "
    "coalesce((alerts_by_severity->>'high')::int, 0) + "
    "coalesce((alerts_by_severity->>'medium')::int, 0) + "
    "coalesce((alerts_by_severity->>'low')::int, 0)"
)


@dataclass(frozen=True)
class OpsWindow:
    start: dt.date
    end: dt.date
    granularity: str
    previous: tuple[dt.date, dt.date] | None
    as_of: dt.date

    def describe(self) -> dict[str, Any]:
        return {
            "from": self.start,
            "to": self.end,
            "granularity": self.granularity,
            "compare_from": self.previous[0] if self.previous else None,
            "compare_to": self.previous[1] if self.previous else None,
        }


def resolve_window(
    conn: psycopg.Connection[Row],
    range_: str,
    start: dt.date | None,
    end: dt.date | None,
    granularity: str,
    compare: bool,
) -> OpsWindow:
    if granularity not in GRANULARITY:
        raise bad_request("granularity must be day, week, or month.", "invalid-granularity")
    row = conn.execute(
        "SELECT greatest(max(day), (SELECT max(created_at)::date FROM alerts)) AS last, "
        "min(day) AS first FROM usage_daily"
    ).fetchone()
    # The window ends at the latest metered day, or at the latest live alert if that is newer.
    as_of = (row or {}).get("last") or dt.datetime.now(dt.UTC).date()
    first = (row or {}).get("first") or as_of
    days = {"30d": 30, "90d": 90, "12m": 365}
    if range_ in days:
        s, e = as_of - dt.timedelta(days=days[range_] - 1), as_of
    elif range_ == "all":
        s, e = first, as_of
    elif range_ == "custom":
        if start is None or end is None or start > end:
            raise bad_request(
                "Custom ranges need from and to, with from not after to.", "invalid-range"
            )
        s, e = start, end
    else:
        raise bad_request("range must be 30d, 90d, 12m, all, or custom.", "invalid-range")
    length = (e - s).days + 1
    previous = (s - dt.timedelta(days=length), s - dt.timedelta(days=1)) if compare else None
    return OpsWindow(s, e, granularity, previous, as_of)


def _change(value: float | None, previous: float | None) -> dict[str, Any]:
    change = None if value is None or previous is None else value - previous
    pct = None if change is None or not previous else round(change / abs(previous) * 100, 2)
    return {"value": value, "previous": previous, "change": change, "change_pct": pct}


def _usage_totals(conn: psycopg.Connection[Row], s: dt.date, e: dt.date) -> Row:
    row = conn.execute(
        f"SELECT coalesce(sum(screenings), 0) AS screenings, "  # noqa: S608  # nosec B608
        f"coalesce(sum({ALERT_SUM}), 0) AS alerts FROM usage_daily WHERE day BETWEEN %s AND %s",
        (s, e),
    ).fetchone()
    assert row is not None
    return row


def _quality(conn: psycopg.Connection[Row], platform: bool, s: dt.date, e: dt.date) -> Row:
    """Precision, false positive rate, and median review minutes for a period."""
    if platform:
        r = conn.execute(
            "SELECT sum(confirmed_true) AS tp, sum(false_positive) AS fp, "
            "sum(median_review_minutes * alerts) / nullif(sum(alerts), 0) AS minutes, "
            "avg(p95_latency_ms) AS p95, avg(p50_latency_ms) AS p50 FROM alert_stats_daily "
            "WHERE day BETWEEN %s AND %s",
            (s, e),
        ).fetchone()
    else:
        r = conn.execute(
            "SELECT count(*) FILTER (WHERE status = 'true_match') AS tp, "
            "count(*) FILTER (WHERE status = 'false_positive') AS fp, "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM resolved_at - "
            "created_at) / 60) FILTER (WHERE resolved_at IS NOT NULL) AS minutes, "
            "(SELECT avg(p95_latency_ms) FROM alert_stats_daily "
            "WHERE day BETWEEN %s AND %s) AS p95, "
            "NULL::numeric AS p50 FROM alerts WHERE resolved_at::date BETWEEN %s AND %s",
            (s, e, s, e),
        ).fetchone()
    assert r is not None
    tp, fp = float(r["tp"] or 0), float(r["fp"] or 0)
    reviewed = tp + fp
    return {
        "precision": (tp / reviewed) if reviewed else None,
        "false_positive_rate": (fp / reviewed) if reviewed else None,
        "median_review_minutes": num(r["minutes"]),
        "p95_latency_ms": num(r["p95"]),
        "reviewed": int(reviewed),
    }


def kpis(conn: psycopg.Connection[Row], w: OpsWindow, platform: bool) -> dict[str, Any]:
    cur_u, cur_q = _usage_totals(conn, w.start, w.end), _quality(conn, platform, w.start, w.end)
    if w.previous:
        prev_u, prev_q = (_usage_totals(conn, *w.previous), _quality(conn, platform, *w.previous))
    else:
        prev_u, prev_q = {}, {}
    created = conn.execute(
        "SELECT count(*) AS n FROM alerts WHERE created_at::date BETWEEN %s AND %s",
        (w.start, w.end),
    ).fetchone()
    return {
        "screenings": _change(num(cur_u["screenings"]), num(prev_u.get("screenings"))),
        "alerts_created": _change(num(cur_u["alerts"]), num(prev_u.get("alerts"))),
        "live_alerts_created": created["n"] if created else 0,
        "precision": _change(cur_q["precision"], prev_q.get("precision")),
        "false_positive_rate": _change(
            cur_q["false_positive_rate"], prev_q.get("false_positive_rate")
        ),
        "median_review_minutes": _change(
            cur_q["median_review_minutes"], prev_q.get("median_review_minutes")
        ),
        "p95_latency_ms": _change(cur_q["p95_latency_ms"], prev_q.get("p95_latency_ms")),
        "reviewed_alerts": cur_q["reviewed"],
        "definitions": "precision = confirmed true matches / reviewed alerts (docs/metrics.md)",
    }


def volume(conn: psycopg.Connection[Row], w: OpsWindow) -> list[Row]:
    rows = conn.execute(
        "SELECT date_trunc(%s, day)::date AS period, sum(screenings) AS screenings, "
        "sum(batch_rows) AS batch_rows, sum(api_calls) AS api_calls FROM usage_daily "
        "WHERE day BETWEEN %s AND %s GROUP BY 1 ORDER BY 1",
        (w.granularity, w.start, w.end),
    ).fetchall()
    return [
        {
            "period": r["period"],
            "screenings": int(r["screenings"]),
            "batch_rows": int(r["batch_rows"]),
            "api_calls": int(r["api_calls"]),
        }
        for r in rows
    ]


def severity(conn: psycopg.Connection[Row], w: OpsWindow) -> list[Row]:
    rows = conn.execute(
        "SELECT date_trunc(%s, day)::date AS period, "
        "sum(coalesce((alerts_by_severity->>'critical')::int, 0)) AS critical, "
        "sum(coalesce((alerts_by_severity->>'high')::int, 0)) AS high, "
        "sum(coalesce((alerts_by_severity->>'medium')::int, 0)) AS medium, "
        "sum(coalesce((alerts_by_severity->>'low')::int, 0)) AS low FROM usage_daily "
        "WHERE day BETWEEN %s AND %s GROUP BY 1 ORDER BY 1",
        (w.granularity, w.start, w.end),
    ).fetchall()
    return [{k: (v if k == "period" else int(v)) for k, v in r.items()} for r in rows]


def quality_series(conn: psycopg.Connection[Row], w: OpsWindow, platform: bool) -> dict[str, Any]:
    if platform:
        rows = conn.execute(
            "SELECT date_trunc(%s, day)::date AS period, sum(confirmed_true) AS tp, "
            "sum(false_positive) AS fp FROM alert_stats_daily WHERE day BETWEEN %s AND %s "
            "GROUP BY 1 ORDER BY 1",
            (w.granularity, w.start, w.end),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT date_trunc(%s, resolved_at)::date AS period, "
            "count(*) FILTER (WHERE status = 'true_match') AS tp, "
            "count(*) FILTER (WHERE status = 'false_positive') AS fp FROM alerts "
            "WHERE resolved_at::date BETWEEN %s AND %s GROUP BY 1 ORDER BY 1",
            (w.granularity, w.start, w.end),
        ).fetchall()
    items = []
    for r in rows:
        tp, fp = float(r["tp"] or 0), float(r["fp"] or 0)
        items.append(
            {
                "period": r["period"],
                "confirmed_true": int(tp),
                "false_positive": int(fp),
                "precision": tp / (tp + fp) if tp + fp else None,
                "false_positive_rate": fp / (tp + fp) if tp + fp else None,
            }
        )
    markers = conn.execute(
        "SELECT day, label FROM business_events WHERE day BETWEEN %s AND %s ORDER BY day",
        (w.start, w.end),
    ).fetchall()
    return {"items": items, "markers": markers}


def latency(conn: psycopg.Connection[Row], w: OpsWindow) -> list[Row]:
    rows = conn.execute(
        "SELECT date_trunc(%s, day)::date AS period, avg(p50_latency_ms) AS p50, "
        "avg(p95_latency_ms) AS p95 FROM alert_stats_daily WHERE day BETWEEN %s AND %s "
        "GROUP BY 1 ORDER BY 1",
        (w.granularity, w.start, w.end),
    ).fetchall()
    return [
        {
            "period": r["period"],
            "p50_ms": round(float(r["p50"]), 1),
            "p95_ms": round(float(r["p95"]), 1),
        }
        for r in rows
    ]


def language_mix(conn: psycopg.Connection[Row], w: OpsWindow) -> list[Row]:
    return conn.execute(
        "SELECT coalesce(a.language, 'unknown') AS language, count(*) AS mentions "
        "FROM mentions m JOIN articles a ON a.id = m.article_id "
        "WHERE coalesce(a.published_at::date, a.harvested_at::date) BETWEEN %s AND %s "
        "GROUP BY 1 ORDER BY 2 DESC, 1",
        (w.start, w.end),
    ).fetchall()


def category_mix(conn: psycopg.Connection[Row], w: OpsWindow) -> list[Row]:
    return conn.execute(
        "SELECT category, severity, count(*) AS findings FROM adverse_findings "
        "WHERE created_at::date BETWEEN %s AND %s GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2",
        (w.start, w.end),
    ).fetchall()


def risk_by_country(conn: psycopg.Connection[Row], w: OpsWindow) -> list[Row]:
    rows = conn.execute(
        "SELECT s.country, count(*) AS alerts, sum(CASE a.severity WHEN 'critical' THEN 4 "
        "WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) AS weighted_alerts FROM alerts a "
        "JOIN subjects s ON s.id = a.subject_id WHERE s.country IS NOT NULL "
        "AND a.created_at::date BETWEEN %s AND %s GROUP BY 1 ORDER BY 3 DESC, 1",
        (w.start, w.end),
    ).fetchall()
    return [
        {
            "country": r["country"],
            "alerts": r["alerts"],
            "weighted_alerts": int(r["weighted_alerts"]),
        }
        for r in rows
    ]


def queue_ageing(
    conn: psycopg.Connection[Row], sla_hours: int, now: dt.datetime | None = None
) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.UTC)
    ages = conn.execute(
        "SELECT extract(epoch FROM (%s::timestamptz - created_at)) / 86400 AS days FROM alerts "
        "WHERE status = ANY(%s)",
        (now, list(OPEN_STATUSES)),
    ).fetchall()
    days = [float(a["days"]) for a in ages]
    buckets = [
        {
            "bucket": name,
            "from_days": lo,
            "to_days": hi,
            "alerts": sum(1 for d in days if d >= lo and (hi is None or d < hi)),
        }
        for name, lo, hi in AGE_BUCKETS
    ]
    return {
        "as_of": now,
        "open_alerts": len(days),
        "sla_hours": sla_hours,
        "sla_breaches": sum(1 for d in days if d * 24 > sla_hours),
        "buckets": buckets,
    }


def throughput(conn: psycopg.Connection[Row], w: OpsWindow) -> list[Row]:
    rows = conn.execute(
        "SELECT date_trunc('week', e.created_at)::date AS week, u.email AS analyst, "
        "count(*) AS resolved "
        "FROM alert_events e JOIN users u ON u.id = e.actor_id "
        "WHERE e.event_type = 'status_change' "
        "AND e.to_status IN ('true_match', 'false_positive', 'closed') "
        "AND e.created_at::date BETWEEN %s AND %s GROUP BY 1, 2 ORDER BY 1, 2",
        (w.start, w.end),
    ).fetchall()
    return [{"week": r["week"], "analyst": r["analyst"], "resolved": r["resolved"]} for r in rows]


def routing(conn: psycopg.Connection[Row]) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT route, CASE WHEN decision = 'nil' THEN 'nil' ELSE 'linked' END AS outcome, "
        "count(*) AS mentions FROM mentions WHERE route IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    names = {
        "accept": "Accepted by BELA",
        "blink": "Adjudicated by BLINK",
        "llm": "Adjudicated by LLM",
        "nil": "NIL (no entity)",
    }
    links: list[dict[str, Any]] = []
    nodes: dict[str, str] = {"mentions": "Mentions", "linked": "Linked entity", "nil": "No entity"}
    for r in rows:
        route = str(r["route"])
        nodes[route] = names.get(route, route)
        links.append({"source": "mentions", "target": r["route"], "value": r["mentions"]})
        links.append({"source": r["route"], "target": r["outcome"], "value": r["mentions"]})
    merged: dict[tuple[str, str], int] = {}
    for link in links:
        merged[(link["source"], link["target"])] = (
            merged.get((link["source"], link["target"]), 0) + link["value"]
        )
    return {
        "total": sum(r["mentions"] for r in rows),
        "nodes": [{"id": k, "label": v} for k, v in nodes.items()],
        "links": [{"source": s, "target": t, "value": v} for (s, t), v in merged.items()],
    }


def hybrid_vs_baseline() -> dict[str, Any]:
    """False matches of the fuzzy baseline, BELA, and the hybrid, from the evaluation harness."""
    if not EVAL_RESULTS.exists():
        return {
            "available": False,
            "reason": "The Phase 3 evaluation has not produced docs/eval/results.json yet.",
        }
    return {
        "available": True,
        "source": "docs/eval/results.json",
        "results": json.loads(EVAL_RESULTS.read_text(encoding="utf-8")),
    }
