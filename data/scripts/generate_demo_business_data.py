"""Generate the synthetic business dataset (PLAN.md section 5.4).

Writes CSV files to data/synthetic/ for 36 months (2023-11-01 to 2026-10-31): plans, tenants,
subscriptions, invoices, invoice lines, daily usage, daily alert statistics, marketing spend, and
business events. The random seed is fixed (42), so two runs produce identical files.

All names, customers, and amounts are fictional. Every figure that comes from this dataset must
be shown with a "Demo data" label.

Usage: python data/scripts/generate_demo_business_data.py [--out DIR]
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

SEED = 42
START = date(2023, 11, 1)
MONTHS = 36
NAMESPACE = uuid.UUID("6f1c7a58-5b0a-4d0e-9f6e-2b0e5d1c9a11")
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "synthetic"

PLANS = [
    ("starter", "Starter", 499, 5000),
    ("professional", "Professional", 1999, 50000),
    ("enterprise", "Enterprise", 8000, 500000),
]
PLAN_MIX = [0.50, 0.35, 0.15]
CHURN = {"starter": 0.030, "professional": 0.015, "enterprise": 0.006}
OVERAGE_PER_1000 = {"starter": 40, "professional": 30, "enterprise": 20}
ANNUAL_SHARE = 0.40
ANNUAL_DISCOUNT = 0.10
EXPANSION_P = 0.04
CONTRACTION_P = 0.01
REGIONS = {
    "North America": (0.45, ["US", "CA"]),
    "EMEA": (0.35, ["GB", "DE", "FR", "NL", "AE", "CH"]),
    "APAC": (0.15, ["SG", "AU", "JP", "IN"]),
    "LATAM": (0.05, ["BR", "MX", "CL"]),
}
INDUSTRIES = ["banking", "payments", "digital assets", "insurance", "legal services", "corporate"]
CHANNELS = ["paid_search", "content", "events", "partners", "outbound"]
CHANNEL_WEIGHTS = [0.25, 0.20, 0.20, 0.20, 0.15]
ADJECTIVES = [
    "Amber",
    "Birch",
    "Cedar",
    "Dune",
    "Ember",
    "Fern",
    "Garnet",
    "Heron",
    "Iris",
    "Juniper",
    "Kestrel",
    "Linden",
    "Maple",
    "Nectar",
    "Onyx",
    "Pebble",
    "Quartz",
    "Russet",
    "Saffron",
    "Tundra",
    "Umber",
    "Violet",
    "Willow",
    "Yarrow",
    "Zephyr",
]
NOUNS = [
    "Lantern",
    "Meadow",
    "Orchard",
    "Harbor",
    "Summit",
    "Ridge",
    "Canyon",
    "Brook",
    "Field",
    "Grove",
    "Island",
    "Junction",
    "Keystone",
    "Lagoon",
    "Mesa",
    "Narrows",
    "Outpost",
    "Prairie",
    "Quarry",
    "Reef",
    "Shore",
    "Terrace",
    "Vale",
    "Wharf",
    "Crossing",
]
SUFFIXES = ["Partners", "Group", "Holdings", "Systems", "Services", "Capital", "Solutions"]
LAUNCH_MONTH = 14  # batch screening launch lifts usage
PRICE_INCREASE_MONTH = 20  # one-time list price increase for new customers
MODEL_UPDATE_MONTH = 22
OPTIMIZATION_MONTH = 26  # latency optimization
EVENTS = [
    (8, "New region: APAC launch"),
    (LAUNCH_MONTH, "Product launch: batch screening"),
    (16, "New region: LATAM launch"),
    (PRICE_INCREASE_MONTH, "Pricing change: list prices up 10 percent for new customers"),
    (MODEL_UPDATE_MONTH, "Linking model update"),
    (OPTIMIZATION_MONTH, "Latency optimization release"),
]


@dataclass
class Tenant:
    id: str
    name: str
    industry: str
    region: str
    country: str
    channel: str
    plan: str
    cycle: str
    monthly: float  # monthly-equivalent price after any annual discount
    start: date
    utilization: float
    batch_share: float
    churned: date | None = None
    open_sub: dict[str, Any] = field(default_factory=dict)


def month_start(index: int) -> date:
    year = START.year + (START.month - 1 + index) // 12
    month = (START.month - 1 + index) % 12 + 1
    return date(year, month, 1)


def month_end(index: int) -> date:
    first = month_start(index)
    return date(first.year, first.month, calendar.monthrange(first.year, first.month)[1])


def sub_price(monthly: float, cycle: str) -> float:
    return round(monthly * 12, 2) if cycle == "annual" else round(monthly, 2)


def list_price(plan: str, rng: np.random.Generator, month: int) -> float:
    if plan == "enterprise":
        base = float(np.clip(rng.lognormal(np.log(14000), 0.45), 8000, 25000))
        base = round(base / 100) * 100
    else:
        base = float({"starter": 499, "professional": 1999}[plan])
    return base * (1.10 if month >= PRICE_INCREASE_MONTH else 1.0)


def make_name(rng: np.random.Generator, used: set[str]) -> str:
    while True:
        name = (
            f"{ADJECTIVES[rng.integers(len(ADJECTIVES))]} {NOUNS[rng.integers(len(NOUNS))]} "
            f"{SUFFIXES[rng.integers(len(SUFFIXES))]}"
        )
        if name not in used:
            used.add(name)
            return name
        name = f"{name} {len(used) % 97 + 2}"
        if name not in used:
            used.add(name)
            return name


def generate() -> dict[str, list[list[Any]]]:
    rng = np.random.default_rng(SEED)
    used_names: set[str] = set()
    tenants: list[Tenant] = []
    subs: list[list[Any]] = []
    region_names = list(REGIONS)
    region_p = [REGIONS[r][0] for r in region_names]
    new_by_month: list[int] = []

    def open_sub(t: Tenant, when: date, reason: str) -> None:
        t.open_sub = {
            "tenant_id": t.id,
            "plan": t.plan,
            "cycle": t.cycle,
            "price": sub_price(t.monthly, t.cycle),
            "start": when,
            "reason": reason,
        }

    def close_sub(t: Tenant, when: date) -> None:
        s = t.open_sub
        subs.append(
            [
                s["tenant_id"],
                s["plan"],
                s["cycle"],
                f"{s['price']:.2f}",
                s["start"].isoformat(),
                when.isoformat(),
                s["reason"],
            ]
        )
        t.open_sub = {}

    for m in range(MONTHS):
        first, last = month_start(m), month_end(m)
        # Existing customers: churn, expansion, contraction (decided once per month).
        for t in tenants:
            if t.churned or not t.open_sub:
                continue
            day = first + timedelta(days=int(rng.integers(0, last.day)))
            if day <= t.open_sub["start"]:
                day = t.open_sub["start"] + timedelta(days=1)
            if day > last:
                continue
            roll = rng.random()
            if roll < CHURN[t.plan]:
                close_sub(t, day)
                t.churned = day
            elif roll < CHURN[t.plan] + EXPANSION_P:
                up = rng.random()
                if t.plan == "starter" and up < 0.35:
                    close_sub(t, day)
                    t.plan = "professional"
                    t.monthly = list_price("professional", rng, 0) * (
                        1 - ANNUAL_DISCOUNT if t.cycle == "annual" else 1
                    )
                    open_sub(t, day, "plan_upgrade")
                elif t.plan == "professional" and up < 0.20:
                    close_sub(t, day)
                    t.plan = "enterprise"
                    t.monthly = list_price("enterprise", rng, 0) * (
                        1 - ANNUAL_DISCOUNT if t.cycle == "annual" else 1
                    )
                    open_sub(t, day, "plan_upgrade")
                else:
                    close_sub(t, day)
                    t.monthly = round(t.monthly * float(rng.uniform(1.10, 1.40)), 2)
                    open_sub(t, day, "expansion")
            elif roll < CHURN[t.plan] + EXPANSION_P + CONTRACTION_P:
                close_sub(t, day)
                t.monthly = round(t.monthly * float(rng.uniform(0.75, 0.90)), 2)
                open_sub(t, day, "contraction")
        # New logos.
        rate = 2 + 5 * m / (MONTHS - 1)
        new = int(rng.poisson(rate))
        new_by_month.append(new)
        for _ in range(new):
            plan = PLANS[int(rng.choice(3, p=PLAN_MIX))][0]
            cycle = "annual" if rng.random() < ANNUAL_SHARE else "monthly"
            region = region_names[int(rng.choice(len(region_names), p=region_p))]
            monthly = list_price(plan, rng, m) * (1 - ANNUAL_DISCOUNT if cycle == "annual" else 1)
            start = first + timedelta(days=int(rng.integers(0, last.day)))
            t = Tenant(
                id=str(uuid.uuid5(NAMESPACE, f"tenant-{len(tenants)}")),
                name=make_name(rng, used_names),
                industry=INDUSTRIES[int(rng.integers(len(INDUSTRIES)))],
                region=region,
                country=REGIONS[region][1][int(rng.integers(len(REGIONS[region][1])))],
                channel=CHANNELS[int(rng.choice(len(CHANNELS), p=CHANNEL_WEIGHTS))],
                plan=plan,
                cycle=cycle,
                monthly=round(monthly, 2),
                start=start,
                utilization=float(np.clip(rng.lognormal(np.log(0.55), 0.35), 0.1, 1.4)),
                batch_share=float(rng.uniform(0.05, 0.30)),
            )
            tenants.append(t)
            open_sub(t, start, "new")
    for t in tenants:
        if t.open_sub:
            s = t.open_sub
            subs.append(
                [
                    s["tenant_id"],
                    s["plan"],
                    s["cycle"],
                    f"{s['price']:.2f}",
                    s["start"].isoformat(),
                    "",
                    s["reason"],
                ]
            )
    subs.sort(key=lambda r: (r[0], r[4], r[1]))

    # Daily usage per tenant.
    quota = {p[0]: p[3] for p in PLANS}
    sub_rows_by_tenant: dict[str, list[list[Any]]] = {}
    for r in subs:
        sub_rows_by_tenant.setdefault(r[0], []).append(r)

    def active_sub(tenant_id: str, when: date) -> list[Any] | None:
        for r in sub_rows_by_tenant[tenant_id]:
            end = date.fromisoformat(r[5]) if r[5] else None
            if date.fromisoformat(r[4]) <= when and (end is None or end > when):
                return r
        return None

    usage_rows: list[list[Any]] = []
    stats: dict[date, list[float]] = {}
    monthly_screenings: dict[tuple[str, int], int] = {}
    total_days = (month_end(MONTHS - 1) - START).days + 1
    days = [START + timedelta(days=i) for i in range(total_days)]
    month_of_day = np.array([(d.year - START.year) * 12 + d.month - START.month for d in days])
    weekday = np.array([d.weekday() for d in days])
    season = np.where(weekday < 5, 1.0, np.where(weekday == 5, 0.35, 0.25))
    season = season / season.mean()
    quarter_end = np.array(
        [
            d.month in (3, 6, 9, 12) and d.day > calendar.monthrange(d.year, d.month)[1] - 5
            for d in days
        ]
    )
    drift = 1 + 0.012 * month_of_day
    lift = np.where(month_of_day >= LAUNCH_MONTH, 1.25, 1.0)
    alert_rate = np.interp(month_of_day, [0, MONTHS - 1], [0.030, 0.016])
    severity_p = [0.04, 0.16, 0.35, 0.45]
    for t in tenants:
        first_idx = (t.start - START).days
        end_date = t.churned or month_end(MONTHS - 1)
        last_idx = min((end_date - START).days, total_days - 1)
        if last_idx < first_idx:
            continue
        idx = np.arange(first_idx, last_idx + 1)
        plans_by_day = [(active_sub(t.id, days[i]) or [None, t.plan])[1] for i in idx]
        quotas = np.array([quota[p] for p in plans_by_day], dtype=float)
        base = quotas * t.utilization / 30.4
        mean = base * season[idx] * drift[idx] * np.where(quarter_end[idx], 1.35, 1.0) * lift[idx]
        screenings = rng.poisson(np.maximum(mean, 0.0))
        batch = (screenings * t.batch_share * (month_of_day[idx] >= LAUNCH_MONTH)).astype(int)
        api = (screenings * 1.4 * rng.uniform(0.9, 1.1, len(idx))).astype(int)
        matched = (screenings * 0.6).astype(int)
        alerts = rng.binomial(screenings, np.minimum(alert_rate[idx], 0.5))
        sev = np.array([rng.multinomial(a, severity_p) for a in alerts]) if len(idx) else None
        for k, i in enumerate(idx):
            d = days[i]
            usage_rows.append(
                [
                    t.id,
                    d.isoformat(),
                    int(screenings[k]),
                    int(batch[k]),
                    int(api[k]),
                    int(matched[k]),
                    json.dumps(
                        {
                            "critical": int(sev[k][0]),
                            "high": int(sev[k][1]),
                            "medium": int(sev[k][2]),
                            "low": int(sev[k][3]),
                        },
                        separators=(",", ":"),
                    ),
                ]
            )
            key = (t.id, int(month_of_day[i]))
            monthly_screenings[key] = monthly_screenings.get(key, 0) + int(screenings[k])
            agg = stats.setdefault(d, [0.0])
            agg[0] += int(alerts[k])
    usage_rows.sort(key=lambda r: (r[0], r[1]))

    # Invoices: one per tenant per month with an active subscription at month end.
    invoices: list[list[Any]] = []
    lines: list[list[Any]] = []
    invoice_id = 0
    for t in tenants:
        for m in range(MONTHS):
            end = month_end(m)
            sub = active_sub(t.id, end)
            if sub is None:
                continue
            plan = sub[1]
            monthly = float(sub[3]) / (12 if sub[2] == "annual" else 1)
            screenings = monthly_screenings.get((t.id, m), 0)
            over = max(0, screenings - quota[plan])
            overage = -(-over // 1000) * OVERAGE_PER_1000[plan]
            invoice_id += 1
            entries = [("subscription", f"{plan} subscription", round(monthly, 2))]
            if overage:
                entries.append(
                    ("overage", f"{-(-over // 1000)} x 1,000 screenings over quota", float(overage))
                )
            if rng.random() < 0.02:
                entries.append(
                    (
                        "credit",
                        "service credit",
                        -round(monthly * float(rng.uniform(0.05, 0.15)), 2),
                    )
                )
            total = round(sum(e[2] for e in entries), 2)
            if m == MONTHS - 1:
                status = "open"
            else:
                u = rng.random()
                recent = m >= MONTHS - 2
                status = (
                    "paid"
                    if u < (0.90 if recent else 0.97)
                    else ("open" if recent and u < 0.97 else "overdue")
                )
            period = month_start(m)
            invoices.append(
                [
                    invoice_id,
                    t.id,
                    period.isoformat(),
                    status,
                    (period + timedelta(days=30)).isoformat(),
                    f"{total:.2f}",
                ]
            )
            for line_type, description, amount in entries:
                lines.append([invoice_id, line_type, description, f"{amount:.2f}"])

    # Daily alert statistics.
    stat_rows: list[list[Any]] = []
    for d in days:
        m = (d.year - START.year) * 12 + d.month - START.month
        alerts = int(stats.get(d, [0.0])[0])
        fp_rate = float(
            np.clip(np.interp(m, [0, MONTHS - 1], [0.80, 0.55]) + rng.normal(0, 0.015), 0.3, 0.95)
        )
        reviewed = int(alerts * 0.9)
        false_positive = int(round(reviewed * fp_rate))
        p50 = (
            float(np.interp(m, [0, OPTIMIZATION_MONTH - 1], [240, 170]))
            if m < OPTIMIZATION_MONTH
            else float(np.interp(m, [OPTIMIZATION_MONTH, MONTHS - 1], [100, 90]))
        )
        p50 = int(round(p50 * float(rng.uniform(0.92, 1.08))))
        review_minutes = float(np.interp(m, [0, MONTHS - 1], [45, 28])) * float(
            rng.uniform(0.9, 1.1)
        )
        stat_rows.append(
            [
                d.isoformat(),
                alerts,
                reviewed - false_positive,
                false_positive,
                f"{review_minutes:.2f}",
                p50,
                int(round(p50 * float(rng.uniform(2.6, 3.4)))),
            ]
        )

    # Marketing spend, monthly by channel.
    spend_rows: list[list[Any]] = []
    for m in range(MONTHS):
        total = (new_by_month[m] + 1) * float(rng.uniform(5500, 8500))
        for channel, weight in zip(CHANNELS, CHANNEL_WEIGHTS, strict=True):
            spend_rows.append(
                [
                    month_start(m).isoformat(),
                    channel,
                    f"{total * weight * float(rng.uniform(0.85, 1.15)):.2f}",
                ]
            )

    tenant_rows = [
        [
            t.id,
            t.name,
            t.industry,
            t.region,
            t.country,
            f"{t.start.isoformat()}T09:00:00+00:00",
            f"{t.churned.isoformat()}T09:00:00+00:00" if t.churned else "",
            t.channel,
        ]
        for t in tenants
    ]
    return {
        "plans": [[p[0], p[1], f"{p[2]:.2f}", p[3]] for p in PLANS],
        "tenants": tenant_rows,
        "subscriptions": subs,
        "invoices": invoices,
        "invoice_lines": lines,
        "usage_daily": usage_rows,
        "alert_stats_daily": stat_rows,
        "marketing_spend": spend_rows,
        "business_events": [
            [month_start(m).replace(day=15).isoformat(), label] for m, label in EVENTS
        ],
    }


HEADERS = {
    "plans": ["code", "name", "monthly_price", "included_screenings"],
    "tenants": [
        "id",
        "name",
        "industry",
        "region",
        "country",
        "created_at",
        "churned_at",
        "acquisition_channel",
    ],
    "subscriptions": [
        "tenant_id",
        "plan",
        "billing_cycle",
        "price",
        "start",
        "end",
        "change_reason",
    ],
    "invoices": ["id", "tenant_id", "period", "status", "due_date", "total"],
    "invoice_lines": ["invoice_id", "line_type", "description", "amount"],
    "usage_daily": [
        "tenant_id",
        "date",
        "screenings",
        "batch_rows",
        "api_calls",
        "articles_matched",
        "alerts_by_severity",
    ],
    "alert_stats_daily": [
        "date",
        "alerts",
        "confirmed_true",
        "false_positive",
        "median_review_minutes",
        "p50_latency_ms",
        "p95_latency_ms",
    ],
    "marketing_spend": ["month", "channel", "spend"],
    "business_events": ["date", "label"],
}


def write(out: Path, data: dict[str, list[list[Any]]]) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, rows in data.items():
        with (out / f"{name}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(HEADERS[name])
            writer.writerows(rows)
        counts[name] = len(rows)
    return counts


def final_arr(data: dict[str, list[list[Any]]]) -> float:
    end = month_end(MONTHS - 1).isoformat()
    total = 0.0
    for r in data["subscriptions"]:
        if r[4] <= end and (not r[5] or r[5] > end):
            total += float(r[3]) / (12 if r[2] == "annual" else 1)
    return total * 12


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    data = generate()
    counts = write(args.out, data)
    for name, count in counts.items():
        print(f"{name}: {count} rows")
    print(f"ending ARR (month-end MRR x 12): {final_arr(data):,.0f} USD")


if __name__ == "__main__":
    main()
