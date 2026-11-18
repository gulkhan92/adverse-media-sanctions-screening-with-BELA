"""Load the synthetic business dataset (data/synthetic/*.csv) and refresh the analytics views.

The load is idempotent: rows for the generated tenants are replaced, other tenants are left
alone. It runs as the table owner (``DATABASE_URL``), never as the API role.
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from atlas.config import DATA_DIR
from atlas.db import connect, copy_rows

DEFAULT_DIR = DATA_DIR / "synthetic"


def read(directory: Path, name: str) -> Iterator[dict[str, str]]:
    with (directory / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def nullable(value: str) -> str | None:
    return value if value != "" else None


def load(directory: Path = DEFAULT_DIR) -> dict[str, int]:
    counts: dict[str, int] = {}
    tenant_ids = [r["id"] for r in read(directory, "tenants")]
    days = [r["date"] for r in read(directory, "alert_stats_daily")]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM invoice_lines WHERE invoice_id IN "
                "(SELECT id FROM invoices WHERE tenant_id = ANY(%s::uuid[]))",
                (tenant_ids,),
            )
            for table in ("invoices", "usage_daily", "subscriptions"):
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = ANY(%s::uuid[])", (tenant_ids,))  # noqa: S608  # nosec B608
            cur.execute("DELETE FROM marketing_spend")
            cur.execute("DELETE FROM business_events")
            cur.execute(
                "DELETE FROM alert_stats_daily WHERE day BETWEEN %s AND %s", (min(days), max(days))
            )
            for r in read(directory, "plans"):
                cur.execute(
                    "INSERT INTO plans (code, name, monthly_price, included_screenings) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (code) DO UPDATE SET "
                    "name = EXCLUDED.name, "
                    "monthly_price = EXCLUDED.monthly_price, "
                    "included_screenings = EXCLUDED.included_screenings",
                    (r["code"], r["name"], r["monthly_price"], r["included_screenings"]),
                )
            cur.execute("SELECT code, id FROM plans")
            plan_id = {code: pid for code, pid in cur.fetchall()}
        with conn.cursor() as cur:  # upsert, so rows that reference a demo tenant survive a reload
            cur.execute(
                "CREATE TEMP TABLE stage_tenants (LIKE tenants INCLUDING DEFAULTS) ON COMMIT DROP"
            )
        counts["tenants"] = copy_rows(
            conn,
            "stage_tenants",
            [
                "id",
                "name",
                "industry",
                "region",
                "country",
                "acquisition_channel",
                "is_demo",
                "created_at",
                "churned_at",
            ],
            (
                (
                    r["id"],
                    r["name"],
                    r["industry"],
                    r["region"],
                    r["country"],
                    r["acquisition_channel"],
                    True,
                    r["created_at"],
                    nullable(r["churned_at"]),
                )
                for r in read(directory, "tenants")
            ),
        )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tenants SELECT * FROM stage_tenants ON CONFLICT (id) DO UPDATE SET "
                "name = EXCLUDED.name, industry = EXCLUDED.industry, region = EXCLUDED.region, "
                "country = EXCLUDED.country, acquisition_channel = EXCLUDED.acquisition_channel, "
                "created_at = EXCLUDED.created_at, churned_at = EXCLUDED.churned_at"
            )
        counts["subscriptions"] = copy_rows(
            conn,
            "subscriptions",
            [
                "tenant_id",
                "plan_id",
                "billing_cycle",
                "price",
                "start_date",
                "end_date",
                "change_reason",
            ],
            (
                (
                    r["tenant_id"],
                    plan_id[r["plan"]],
                    r["billing_cycle"],
                    r["price"],
                    r["start"],
                    nullable(r["end"]),
                    r["change_reason"],
                )
                for r in read(directory, "subscriptions")
            ),
        )
        counts["invoices"] = copy_rows(
            conn,
            "invoices",
            ["id", "tenant_id", "period", "status", "due_date", "total"],
            (
                (r["id"], r["tenant_id"], r["period"], r["status"], r["due_date"], r["total"])
                for r in read(directory, "invoices")
            ),
        )
        counts["invoice_lines"] = copy_rows(
            conn,
            "invoice_lines",
            ["invoice_id", "line_type", "description", "amount"],
            (
                (r["invoice_id"], r["line_type"], r["description"], r["amount"])
                for r in read(directory, "invoice_lines")
            ),
        )
        counts["usage_daily"] = copy_rows(
            conn,
            "usage_daily",
            [
                "tenant_id",
                "day",
                "screenings",
                "batch_rows",
                "api_calls",
                "articles_matched",
                "alerts_by_severity",
            ],
            (
                (
                    r["tenant_id"],
                    r["date"],
                    r["screenings"],
                    r["batch_rows"],
                    r["api_calls"],
                    r["articles_matched"],
                    r["alerts_by_severity"],
                )
                for r in read(directory, "usage_daily")
            ),
        )
        counts["alert_stats_daily"] = copy_rows(
            conn,
            "alert_stats_daily",
            [
                "day",
                "alerts",
                "confirmed_true",
                "false_positive",
                "median_review_minutes",
                "p50_latency_ms",
                "p95_latency_ms",
            ],
            (
                (
                    r["date"],
                    r["alerts"],
                    r["confirmed_true"],
                    r["false_positive"],
                    r["median_review_minutes"],
                    r["p50_latency_ms"],
                    r["p95_latency_ms"],
                )
                for r in read(directory, "alert_stats_daily")
            ),
        )
        counts["marketing_spend"] = copy_rows(
            conn,
            "marketing_spend",
            ["month", "channel", "spend"],
            ((r["month"], r["channel"], r["spend"]) for r in read(directory, "marketing_spend")),
        )
        counts["business_events"] = copy_rows(
            conn,
            "business_events",
            ["day", "label"],
            ((r["date"], r["label"]) for r in read(directory, "business_events")),
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT setval('invoices_id_seq', (SELECT max(id) FROM invoices)), "
                "setval('invoice_lines_id_seq', (SELECT max(id) FROM invoice_lines))"
            )
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT refresh_business_views()")
        conn.commit()
    with connect(autocommit=True) as conn:
        for table in ("subscriptions", "invoices", "usage_daily", "tenant_mrr_monthly"):
            conn.execute(f"ANALYZE {table}")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args: Any = parser.parse_args()
    for name, count in load(args.dir).items():
        print(f"{name}: {count} rows")


if __name__ == "__main__":
    main()
