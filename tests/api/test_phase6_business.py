"""Business data, revenue views, metrics, forecast, filters, caching, export, and performance."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import statistics
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pyotp
import pytest
from conftest import ADMIN_URL, API_DIR, MFA_SECRET, PASSWORD, _app_url

BIZ_DB = "atlas_biz_test"
SCRIPT = Path(__file__).resolve().parents[2] / "data" / "scripts" / "generate_demo_business_data.py"


def load_generator() -> Any:
    spec = importlib.util.spec_from_file_location("generate_demo_business_data", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_demo_business_data"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def biz(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """A database with all migrations, the generated dataset loaded, and an API client."""
    from atlas import db as dbmodule
    from atlas.api.app import create_app
    from atlas.api.security import make_hasher
    from atlas.api.settings import Settings
    from atlas.etl import business
    from fastapi.testclient import TestClient

    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not reachable (start it with infra/dev_db.sh start)")
    admin.execute(f"DROP DATABASE IF EXISTS {BIZ_DB} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {BIZ_DB}")
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{BIZ_DB}"
    import os

    env = {**os.environ, "DATABASE_URL": url, "PYTHONPATH": str(API_DIR)}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    out = tmp_path_factory.mktemp("synthetic")
    generator = load_generator()
    data = generator.generate()
    generator.write(out, data)
    original = dbmodule.DATABASE_URL
    dbmodule.DATABASE_URL = url
    try:
        counts = business.load(out)
    finally:
        dbmodule.DATABASE_URL = original
    settings = Settings(
        database_url=_app_url(url),
        argon_time_cost=1,
        argon_memory_kib=8192,
        argon_parallelism=1,
        limit_ip=(10**6, 60),
        limit_user=(10**6, 60),
    )
    app = create_app(settings)
    owner = psycopg.connect(url, autocommit=True)
    hasher = make_hasher(settings)
    pw = hasher.hash(PASSWORD)
    tenants = [
        r[0]
        for r in owner.execute("SELECT id FROM tenants ORDER BY created_at, id LIMIT 2").fetchall()
    ]
    users = {}
    for key, tenant, role in (
        ("platform", None, "platform_admin"),
        ("finance", None, "finance_analyst"),
        ("mgr1", tenants[0], "manager"),
        ("mgr2", tenants[1], "manager"),
        ("viewer1", tenants[0], "viewer"),
        ("tadmin1", tenants[0], "tenant_admin"),
    ):
        users[key] = owner.execute(
            "INSERT INTO users (tenant_id, email, password_hash, role) VALUES (%s, %s, %s, %s) "
            "RETURNING id",
            (tenant, f"{key}@example.org", pw, role),
        ).fetchone()[0]  # type: ignore[index]
        if role in ("platform_admin", "tenant_admin"):
            owner.execute(
                "INSERT INTO user_mfa (user_id, totp_secret_enc, recovery_hashes, enabled_at) "
                "VALUES (%s, pgp_sym_encrypt(%s, %s), %s, now())",
                (users[key], MFA_SECRET, settings.data_key, []),
            )
    with TestClient(app, base_url="https://testserver") as _:
        yield {
            "app": app,
            "owner": owner,
            "tenants": tenants,
            "users": users,
            "counts": counts,
            "data": data,
            "dir": out,
            "generator": generator,
            "url": url,
        }
    owner.close()
    admin.execute(f"DROP DATABASE IF EXISTS {BIZ_DB} WITH (FORCE)")
    admin.close()


@pytest.fixture()
def sign_in(biz: dict[str, Any]) -> Any:
    from fastapi.testclient import TestClient

    biz["app"].state.atlas.limiter.reset()

    def build(key: str | None = None) -> Any:
        client = TestClient(biz["app"], base_url="https://testserver")
        if key is None:
            return client
        reply = client.post(
            "/api/v1/auth/login", json={"email": f"{key}@example.org", "password": PASSWORD}
        )
        assert reply.status_code == 200, reply.text
        body = reply.json()
        if body["status"] == "mfa_required":
            ok = client.post(
                "/api/v1/auth/mfa/verify",
                json={"mfa_token": body["mfa_token"], "code": pyotp.TOTP(MFA_SECRET).now()},
            )
            assert ok.status_code == 200, ok.text
        client.headers["X-CSRF-Token"] = client.cookies.get("atlas_csrf")
        return client

    return build


@pytest.fixture()
def finance(sign_in: Any, biz: dict[str, Any]) -> Any:
    biz["app"].state.atlas.extras["cache"].clear()
    return sign_in("finance")


def q(owner: Any, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    return owner.execute(sql, params).fetchall()


# --- Generator -----------------------------------------------------------------------


def digest(directory: Path) -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(directory.glob("*.csv"))
    }


def test_generator_is_deterministic(biz: dict[str, Any], tmp_path: Path) -> None:
    generator = biz["generator"]
    generator.write(tmp_path, generator.generate())
    assert digest(tmp_path) == digest(biz["dir"]) and len(digest(tmp_path)) == 9


def test_generator_envelope_and_shape(biz: dict[str, Any]) -> None:
    data, generator = biz["data"], biz["generator"]
    arr = generator.final_arr(data)
    assert 5_000_000 <= arr <= 9_000_000
    assert len({r[1] for r in data["tenants"]}) == len(data["tenants"])  # unique fictional names
    months = {r[0][:7] for r in data["alert_stats_daily"]}
    assert len(months) == 36 and min(months) == "2023-11" and max(months) == "2026-10"
    plans = [r[1] for r in data["subscriptions"] if r[6] == "new"]
    share = {p: plans.count(p) / len(plans) for p in ("starter", "professional", "enterprise")}
    assert 0.40 < share["starter"] < 0.60 and 0.25 < share["professional"] < 0.45
    cycles = [r[0] for r in data["subscriptions"] if r[6] == "new"]
    assert cycles  # signups exist for every plan and cycle
    annual = sum(1 for r in data["subscriptions"] if r[6] == "new" and r[2] == "annual")
    assert 0.25 < annual / len(cycles) < 0.55


def test_load_is_idempotent(biz: dict[str, Any]) -> None:
    from atlas import db as dbmodule
    from atlas.etl import business

    before = q(biz["owner"], "SELECT count(*) FROM tenants")[0][0]
    original = dbmodule.DATABASE_URL
    dbmodule.DATABASE_URL = biz["url"]
    try:
        counts = business.load(biz["dir"])
    finally:
        dbmodule.DATABASE_URL = original
    assert q(biz["owner"], "SELECT count(*) FROM tenants")[0][0] == before == counts["tenants"]


def test_mrr_is_continuous_and_nonnegative(biz: dict[str, Any]) -> None:
    rows = q(biz["owner"], "SELECT month_end, mrr FROM mrr_monthly ORDER BY month_end")
    values = [float(r[1]) for r in rows]
    assert len(values) == 36 and all(v >= 0 for v in values)
    # The first months have a tiny base, so single enterprise signups move MRR a lot.
    steady = list(zip(values[5:], values[6:], strict=False))
    assert all(abs(b - a) / a < 0.40 for a, b in steady)
    assert values[-1] > values[0]


def test_invoices_reconcile_to_subscriptions_and_overage(biz: dict[str, Any]) -> None:
    owner = biz["owner"]
    lines = q(
        owner,
        "SELECT count(*) FROM invoices i WHERE abs(i.total - (SELECT sum(amount) "
        "FROM invoice_lines l WHERE l.invoice_id = i.id)) > 0.005",
    )
    assert lines[0][0] == 0
    # Billed subscription lines equal the MRR of the same month.
    diff = q(
        owner,
        "SELECT count(*) FROM usage_revenue_monthly u JOIN mrr_monthly m "
        "ON m.month_end = (u.month + interval '1 month - 1 day')::date "
        "WHERE abs(u.subscription - m.mrr) > 0.05",
    )
    assert diff[0][0] == 0
    assert q(owner, "SELECT count(*) FROM invoice_lines WHERE line_type = 'overage'")[0][0] > 0
    assert q(owner, "SELECT count(*) FROM usage_revenue_monthly")[0][0] == 36


# --- Views and reconciliation --------------------------------------------------------


def test_movements_reconcile_to_mrr_change_every_month(biz: dict[str, Any]) -> None:
    rows = q(
        biz["owner"],
        "SELECT month_end, begin_mrr, end_mrr, new_mrr, expansion_mrr, "
        "contraction_mrr, churn_mrr FROM mrr_movements ORDER BY month_end",
    )
    assert len(rows) == 36
    previous_end = 0.0
    for _, begin, end, new, exp, contr, churn in rows:
        assert abs(float(begin) - previous_end) < 0.005
        assert (
            abs(float(begin) + float(new) + float(exp) - float(contr) - float(churn) - float(end))
            < 0.01
        )
        previous_end = float(end)


def test_tenant_view_sums_to_total_mrr(biz: dict[str, Any]) -> None:
    diff = q(
        biz["owner"],
        "SELECT count(*) FROM (SELECT month_end, sum(mrr) AS s FROM "
        "tenant_mrr_monthly GROUP BY 1) t JOIN mrr_monthly m USING (month_end) "
        "WHERE abs(t.s - m.mrr) > 0.005",
    )
    assert diff[0][0] == 0
    by_dim = q(
        biz["owner"],
        "SELECT dimension, count(DISTINCT month_end), "
        "max(abs(s - m.mrr)) FROM (SELECT month_end, dimension, sum(mrr) AS s "
        "FROM revenue_by_dimension GROUP BY 1, 2) d JOIN mrr_monthly m "
        "USING (month_end) GROUP BY dimension",
    )
    assert len(by_dim) == 4 and all(float(r[2]) < 0.01 for r in by_dim)


def test_api_movements_reconcile(finance: Any) -> None:
    body = finance.get("/api/v1/platform/revenue/movements", params={"range": "all"}).json()
    assert body["demo_data"] is True and len(body["items"]) == 36
    for i in body["items"]:
        assert (
            abs(
                i["new"]
                + i["expansion"]
                + i["contraction"]
                + i["churn"]
                - (i["end_mrr"] - i["begin_mrr"])
            )
            < 0.01
        )
        assert abs(i["net_new"] - (i["end_mrr"] - i["begin_mrr"])) < 0.01
        assert i["contraction"] <= 0 and i["churn"] <= 0


def test_refresh_is_concurrent_and_clears_cache(finance: Any, biz: dict[str, Any]) -> None:
    first = finance.get("/api/v1/platform/revenue/mrr")
    assert first.headers["x-cache"] == "miss"
    assert finance.get("/api/v1/platform/revenue/mrr").headers["x-cache"] == "hit"
    stamp = finance.post("/api/v1/platform/revenue/refresh")
    assert stamp.status_code == 200 and stamp.json()["refreshed_at"]
    assert finance.get("/api/v1/platform/revenue/mrr").headers["x-cache"] == "miss"
    audit = q(
        biz["owner"], "SELECT count(*) FROM audit_log WHERE action = 'analytics.views_refreshed'"
    )
    assert audit[0][0] >= 1
    assert finance.get("/api/v1/platform/revenue/mrr").json()["refreshed_at"]


# --- Metric definitions --------------------------------------------------------------


def series(finance: Any, **params: Any) -> dict[str, dict[str, Any]]:
    body = finance.get("/api/v1/platform/revenue/kpis", params={"range": "all", **params}).json()
    return body["kpis"]


def test_kpi_definitions_match_independent_calculation(finance: Any, biz: dict[str, Any]) -> None:
    owner = biz["owner"]
    kpis = series(finance)
    last = q(
        owner, "SELECT month_end, mrr, active_customers FROM mrr_monthly ORDER BY 1 DESC LIMIT 1"
    )[0]
    mrr, customers = float(last[1]), int(last[2])
    assert kpis["mrr"]["value"] == pytest.approx(mrr, abs=0.01)
    assert kpis["arr"]["value"] == pytest.approx(mrr * 12, abs=0.1)
    assert kpis["active_customers"]["value"] == customers
    assert kpis["arpa"]["value"] == pytest.approx(mrr / customers, abs=0.01)
    mv = q(
        owner,
        "SELECT new_mrr, expansion_mrr, contraction_mrr, churn_mrr, churned_customers, "
        "begin_customers FROM mrr_movements ORDER BY month_end DESC LIMIT 1",
    )[0]
    new, exp, contr, churn, churned, begin = (float(x) for x in mv)
    assert kpis["net_new_mrr"]["value"] == pytest.approx(new + exp - contr - churn, abs=0.01)
    assert kpis["quick_ratio"]["value"] == pytest.approx((new + exp) / (contr + churn), abs=1e-6)
    assert kpis["logo_churn"]["value"] == pytest.approx(churned / begin, abs=1e-9)
    # Net and gross revenue retention over 12 months, recomputed from tenant rows.
    rows = q(
        owner,
        "SELECT cur.mrr, prev.mrr FROM tenant_mrr_monthly prev JOIN tenant_mrr_monthly cur "
        "ON cur.tenant_id = prev.tenant_id AND cur.month_end = (SELECT max(month_end) "
        "FROM tenant_mrr_monthly) WHERE prev.mrr > 0 AND prev.month_end = "
        "(SELECT (max(month_end) - interval '12 months')::date FROM tenant_mrr_monthly)",
    )
    base = sum(float(p) for _, p in rows)
    nrr = sum(float(c) for c, _ in rows) / base
    grr = sum(min(float(c), float(p)) for c, p in rows) / base
    assert rows and kpis["nrr"]["value"] == pytest.approx(nrr, abs=1e-6)
    assert kpis["grr"]["value"] == pytest.approx(grr, abs=1e-6)
    assert 0 < kpis["grr"]["value"] <= 1.0 and kpis["grr"]["value"] <= kpis["nrr"]["value"]


def test_unit_economics_formulas(finance: Any) -> None:
    body = finance.get("/api/v1/platform/revenue/unit-economics", params={"range": "12m"}).json()
    assert body["gross_margin"] == 0.8
    row = body["items"][-1]
    assert row["cac_payback_months"] == pytest.approx(row["cac_t3"] / (row["arpa"] * 0.8), rel=1e-9)
    assert row["ltv"] == pytest.approx(row["arpa"] * 0.8 / row["monthly_churn_t6"], rel=1e-9)
    assert row["ltv_cac"] == pytest.approx(row["ltv"] / row["cac_t3"], rel=1e-9)
    month = next(i for i in reversed(body["items"]) if i["cac"] and i["new_customers"])
    assert month["cac"] == pytest.approx(
        month["marketing_spend"] / month["new_customers"], rel=1e-9
    )
    channels = body["by_channel"]
    assert {c["channel"] for c in channels} == {
        "paid_search",
        "content",
        "events",
        "partners",
        "outbound",
    }
    assert all(c["cac"] is None or c["cac"] > 0 for c in channels)


def test_cac_and_payback_follow_the_marketing_data(finance: Any, biz: dict[str, Any]) -> None:
    owner = biz["owner"]
    spend = float(
        q(owner, "SELECT sum(spend) FROM marketing_spend WHERE month >= '2026-08-01'")[0][0]
    )
    new = int(
        q(owner, "SELECT sum(new_customers) FROM mrr_movements WHERE month_end >= '2026-08-01'")[0][
            0
        ]
    )
    kpis = series(finance)
    arpa = kpis["arpa"]["value"]
    assert kpis["cac_payback_months"]["value"] == pytest.approx(
        (spend / new) / (arpa * 0.8), rel=1e-6
    )


# --- Windows, comparison, filters ----------------------------------------------------


def test_range_presets_and_custom_ranges(finance: Any) -> None:
    def months(**p: Any) -> int:
        return finance.get("/api/v1/platform/revenue/mrr", params=p).json()["window"]["months"]

    assert months(range="30d") == 1 and months(range="90d") == 3
    assert months(range="12m") == 12 and months(range="all") == 36
    custom = finance.get(
        "/api/v1/platform/revenue/mrr",
        params={"range": "custom", "from": "2025-01-01", "to": "2025-06-30"},
    ).json()
    assert custom["window"]["months"] == 6 and custom["items"][0]["month_end"] == "2025-01-31"
    for bad in (
        {"range": "custom"},
        {"range": "custom", "from": "2025-06-01", "to": "2025-01-01"},
        {"range": "custom", "from": "2030-01-01", "to": "2030-02-01"},
        {"range": "7d"},
    ):
        assert finance.get("/api/v1/platform/revenue/mrr", params=bad).status_code in (400, 422), (
            bad
        )


def test_revenue_is_monthly_only(finance: Any) -> None:
    for g in ("day", "week"):
        reply = finance.get("/api/v1/platform/revenue/kpis", params={"granularity": g})
        assert reply.status_code == 400 and "monthly" in reply.json()["detail"]


def test_comparison_period(finance: Any) -> None:
    body = finance.get(
        "/api/v1/platform/revenue/kpis", params={"range": "90d", "compare": "true"}
    ).json()
    w = body["window"]
    assert (
        w["months"] == 3 and w["compare_to"] == "2026-07-31" and w["compare_from"] == "2026-05-31"
    )
    mrr = body["kpis"]["mrr"]
    assert mrr["change"] == pytest.approx(mrr["value"] - mrr["previous"], abs=1e-6)
    assert mrr["change_pct"] == pytest.approx(100 * mrr["change"] / mrr["previous"], abs=0.01)
    assert len(mrr["sparkline"]) == 3
    none = finance.get("/api/v1/platform/revenue/kpis", params={"range": "90d"}).json()
    assert none["kpis"]["mrr"]["previous"] is None


def test_filters_partition_the_total(finance: Any) -> None:
    total = series(finance)["mrr"]["value"]
    for dimension, values in (
        ("region", ["North America", "EMEA", "APAC", "LATAM"]),
        ("plan", ["starter", "professional", "enterprise"]),
        (
            "industry",
            ["banking", "payments", "digital assets", "insurance", "legal services", "corporate"],
        ),
    ):
        parts = [series(finance, **{dimension: v})["mrr"]["value"] for v in values]
        assert sum(p for p in parts) == pytest.approx(total, abs=0.05), dimension
    channels = ["paid_search", "content", "events", "partners", "outbound"]
    assert sum(series(finance, channel=c)["mrr"]["value"] for c in channels) == pytest.approx(
        total, abs=0.05
    )
    assert finance.get("/api/v1/platform/revenue/kpis", params={"plan": "gold"}).status_code == 422


def test_tenant_filters_disable_cac_metrics(finance: Any) -> None:
    body = finance.get(
        "/api/v1/platform/revenue/kpis", params={"range": "all", "region": "EMEA"}
    ).json()
    assert body["kpis"]["cac_payback_months"]["value"] is None
    assert any("CAC" in n for n in body["notes"])
    assert body["kpis"]["mrr"]["value"] > 0


def test_stacked_mrr_by_plan_sums_to_total(finance: Any) -> None:
    body = finance.get("/api/v1/platform/revenue/mrr", params={"range": "all"}).json()
    for item in body["items"]:
        assert sum(item["by_plan"].values()) == pytest.approx(item["mrr"], abs=0.01)
    assert {e["label"] for e in body["events"]} >= {"Product launch: batch screening"}


def test_mix_dimensions_concentration_cohorts(finance: Any) -> None:
    mix = finance.get("/api/v1/platform/revenue/mix", params={"range": "12m"}).json()
    assert len(mix["items"]) == 12 and all(i["overage"] >= 0 for i in mix["items"])
    assert any(i["overage"] > 0 for i in mix["items"])
    for by in ("plan", "region", "industry", "channel"):
        dim = finance.get("/api/v1/platform/revenue/dimensions", params={"by": by}).json()
        assert sum(i["share_pct"] for i in dim["items"]) == pytest.approx(100, abs=0.5)
        assert dim["items"] == sorted(dim["items"], key=lambda i: i["mrr"], reverse=True)
    assert (
        finance.get("/api/v1/platform/revenue/dimensions", params={"by": "color"}).status_code
        == 422
    )
    top = finance.get("/api/v1/platform/revenue/concentration").json()
    cumulative = [i["cumulative_share_pct"] for i in top["items"]]
    assert len(top["items"]) == 10 and cumulative == sorted(cumulative) and cumulative[-1] < 100
    assert top["items"][0]["share_pct"] >= top["items"][-1]["share_pct"]
    cohorts = finance.get("/api/v1/platform/revenue/cohorts").json()["items"]
    first = [c for c in cohorts if c["months_since"] == 0]
    assert first and all(abs(c["retention_pct"] - 100) < 0.01 for c in first)


def test_billing_health(finance: Any) -> None:
    body = finance.get("/api/v1/platform/revenue/billing-health").json()
    statuses = {s["status"] for s in body["status"]}
    assert {"paid", "open"} <= statuses
    assert sum(b["invoices"] for b in body["aging"]) == len(body["items"])
    assert all(i["status"] in ("open", "overdue") for i in body["items"])
    assert sum(b["amount"] for b in body["aging"]) == pytest.approx(
        sum(i["total"] for i in body["items"]), abs=0.05
    )


def test_customer_table_and_drilldown(finance: Any) -> None:
    page = finance.get("/api/v1/platform/revenue/customers", params={"limit": 10}).json()
    assert len(page["items"]) == 10 and page["total"] == 121
    mrrs = [i["mrr"] for i in page["items"]]
    assert mrrs == sorted(mrrs, reverse=True)
    first = page["items"][0]
    assert len(first["mrr_history"]) <= 12 and first["health"] in ("healthy", "at_risk")
    by_name = finance.get(
        "/api/v1/platform/revenue/customers",
        params={"sort": "name", "descending": "false", "limit": 5},
    ).json()
    names = [i["name"] for i in by_name["items"]]
    assert names == sorted(names)
    found = finance.get(
        "/api/v1/platform/revenue/customers", params={"search": first["name"][:6]}
    ).json()
    assert any(i["tenant_id"] == first["tenant_id"] for i in found["items"])
    paged = finance.get(
        "/api/v1/platform/revenue/customers", params={"limit": 10, "offset": 10}
    ).json()
    assert {i["tenant_id"] for i in paged["items"]}.isdisjoint(
        {i["tenant_id"] for i in page["items"]}
    )
    filtered = finance.get(
        "/api/v1/platform/revenue/customers", params={"plan": "enterprise"}
    ).json()
    assert filtered["items"] and all(i["plan"] == "enterprise" for i in filtered["items"])
    detail = finance.get(f"/api/v1/platform/revenue/customers/{first['tenant_id']}").json()
    assert detail["tenant"]["name"] == first["name"] and detail["subscriptions"]
    assert detail["mrr_series"] and detail["usage_monthly"] and detail["invoices"]
    assert (
        finance.get("/api/v1/platform/revenue/customers", params={"sort": "bogus"}).status_code
        == 422
    )
    missing = finance.get("/api/v1/platform/revenue/customers/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404


# --- Forecast ------------------------------------------------------------------------


def test_forecast_is_labeled_and_well_formed(finance: Any) -> None:
    one = finance.get("/api/v1/platform/revenue/forecast").json()
    assert one["label"] == "Estimate" and "ETS" in one["method"] and one["demo_data"] is True
    assert one["history_months"] == 36 and len(one["projection"]) == 6
    first = one["projection"][0]["month_end"]
    assert first == "2026-11-30" and one["projection"][-1]["month_end"] == "2027-04-30"
    for p in one["projection"]:
        assert 0 <= p["lower"] <= p["mean"] <= p["upper"]
    widths = [p["upper"] - p["lower"] for p in one["projection"]]
    assert widths == sorted(widths)  # uncertainty grows with the horizon
    assert one["backtest"]["holdout_months"] == 6 and one["backtest"]["mape_pct"] < 15
    assert one["projection"][0]["mean"] == pytest.approx(one["history"][-1]["mrr"], rel=0.15)
    finance.post("/api/v1/platform/revenue/refresh")
    two = finance.get("/api/v1/platform/revenue/forecast").json()
    assert two["projection"] == one["projection"]  # deterministic


def test_forecast_needs_enough_history() -> None:
    from atlas.api import revenue
    from atlas.api.errors import ApiError

    history = [(dt.date(2025, m, 28), 1000.0 + m) for m in range(1, 13)]
    with pytest.raises(ApiError):
        revenue.forecast(history)


# --- Export, authorization, performance ----------------------------------------------


def test_csv_export(finance: Any, biz: dict[str, Any]) -> None:
    reply = finance.get(
        "/api/v1/platform/revenue/movements", params={"range": "12m", "format": "csv"}
    )
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/csv")
    assert "attachment" in reply.headers["content-disposition"]
    lines = reply.text.strip().splitlines()
    assert lines[0].startswith("month_end,begin_mrr,new") and len(lines) == 13
    kpi_csv = finance.get(
        "/api/v1/platform/revenue/kpis", params={"format": "csv"}
    ).text.splitlines()
    assert kpi_csv[0] == "kpi,month_end,value" and len(kpi_csv) > 10
    assert (
        q(biz["owner"], "SELECT count(*) FROM audit_log WHERE action = 'analytics.exported'")[0][0]
        >= 2
    )
    assert (
        finance.get(
            "/api/v1/platform/revenue/customers/00000000-0000-0000-0000-000000000000",
            params={"format": "csv"},
        ).status_code
        == 404
    )


def test_revenue_authorization(sign_in: Any) -> None:
    paths = [
        "/api/v1/platform/revenue/summary",
        "/api/v1/platform/revenue/kpis",
        "/api/v1/platform/revenue/forecast",
        "/api/v1/platform/revenue/customers",
    ]
    for role, allowed in (
        ("finance", True),
        ("platform", True),
        ("mgr1", False),
        ("tadmin1", False),
        ("viewer1", False),
    ):
        client = sign_in(role)
        for path in paths:
            code = client.get(path).status_code
            assert (code == 200) is allowed, (role, path, code)
    assert sign_in().get(paths[0]).status_code == 401
    assert sign_in("mgr1").post("/api/v1/platform/revenue/refresh").status_code == 403


def test_platform_header_does_not_narrow_revenue_scope(sign_in: Any, biz: dict[str, Any]) -> None:
    client = sign_in("platform")
    plain = client.get("/api/v1/platform/revenue/customers").json()["total"]
    client.headers["X-Tenant-Id"] = str(biz["tenants"][0])
    assert client.get("/api/v1/platform/revenue/customers").json()["total"] == plain


def test_revenue_endpoints_respond_within_500_ms(finance: Any, biz: dict[str, Any]) -> None:
    cache = biz["app"].state.atlas.extras["cache"]
    paths = [
        "summary",
        "kpis",
        "mrr",
        "movements",
        "mix",
        "forecast",
        "cohorts",
        "dimensions",
        "concentration",
        "unit-economics",
        "billing-health",
        "customers",
    ]
    slowest = {}
    for path in paths:
        times = []
        for params in (
            {"range": "all"},
            {"range": "12m", "compare": "true"},
            {"range": "all", "region": "EMEA"},
        ):
            cache.clear()
            started = time.perf_counter()
            reply = finance.get(f"/api/v1/platform/revenue/{path}", params=params)
            times.append((time.perf_counter() - started) * 1000)
            assert reply.status_code == 200, (path, reply.text)
        slowest[path] = max(times)
    print("REVENUE_LATENCY_MS", {k: round(v, 1) for k, v in slowest.items()})
    assert max(slowest.values()) < 500, slowest
    assert statistics.median(slowest.values()) < 250, slowest
    cached = time.perf_counter()
    finance.get("/api/v1/platform/revenue/kpis", params={"range": "all"})
    assert (time.perf_counter() - cached) * 1000 < 500


# --- Operational analytics -----------------------------------------------------------


def window_sum(owner: Any, sql: str, *params: Any) -> float:
    return float(q(owner, sql, *params)[0][0] or 0)


def test_operational_kpis_for_a_tenant_come_from_its_own_rows(
    sign_in: Any, biz: dict[str, Any]
) -> None:
    owner, t1, t2 = biz["owner"], biz["tenants"][0], biz["tenants"][1]
    biz["app"].state.atlas.extras["cache"].clear()
    one, two = sign_in("mgr1"), sign_in("mgr2")
    a = one.get("/api/v1/analytics/kpis", params={"range": "all"}).json()
    b = two.get("/api/v1/analytics/kpis", params={"range": "all"}).json()
    expected_a = window_sum(
        owner, "SELECT sum(screenings) FROM usage_daily WHERE tenant_id = %s", t1
    )
    expected_b = window_sum(
        owner, "SELECT sum(screenings) FROM usage_daily WHERE tenant_id = %s", t2
    )
    assert a["kpis"]["screenings"]["value"] == expected_a
    assert b["kpis"]["screenings"]["value"] == expected_b and expected_a != expected_b
    assert a["demo_data"] is True and "synthetic" in a["label"]


def test_volume_granularity_and_totals(sign_in: Any, biz: dict[str, Any]) -> None:
    client = sign_in("platform")
    total = window_sum(biz["owner"], "SELECT sum(screenings) FROM usage_daily")
    for granularity, expected_min in (("day", 1000), ("week", 150), ("month", 36)):
        body = client.get(
            "/api/v1/analytics/volume", params={"range": "all", "granularity": granularity}
        ).json()
        assert sum(i["screenings"] for i in body["items"]) == total
        assert len(body["items"]) >= expected_min
    assert client.get("/api/v1/analytics/volume", params={"granularity": "year"}).status_code == 422
    last30 = client.get(
        "/api/v1/analytics/volume", params={"range": "30d", "granularity": "day"}
    ).json()
    assert len(last30["items"]) == 30 and last30["window"]["to"] == "2026-10-31"


def test_severity_series_matches_usage(sign_in: Any, biz: dict[str, Any]) -> None:
    body = sign_in("platform").get("/api/v1/analytics/severity", params={"range": "all"}).json()
    totals = {k: sum(i[k] for i in body["items"]) for k in ("critical", "high", "medium", "low")}
    expected = window_sum(
        biz["owner"], "SELECT sum((alerts_by_severity->>'high')::int) FROM usage_daily"
    )
    assert totals["high"] == expected and totals["low"] > totals["critical"] > 0


def test_quality_precision_and_version_markers(sign_in: Any, biz: dict[str, Any]) -> None:
    client = sign_in("platform")
    body = client.get(
        "/api/v1/analytics/quality", params={"range": "all", "granularity": "month"}
    ).json()
    tp = window_sum(biz["owner"], "SELECT sum(confirmed_true) FROM alert_stats_daily")
    fp = window_sum(biz["owner"], "SELECT sum(false_positive) FROM alert_stats_daily")
    assert sum(i["confirmed_true"] for i in body["items"]) == tp
    first, last = body["items"][0], body["items"][-1]
    assert last["precision"] > first["precision"]  # false positives trend down
    assert last["false_positive_rate"] == pytest.approx(1 - last["precision"])
    assert {m["label"] for m in body["markers"]} >= {"Linking model update"}
    kpis = client.get("/api/v1/analytics/kpis", params={"range": "all"}).json()["kpis"]
    assert kpis["precision"]["value"] == pytest.approx(tp / (tp + fp))
    assert kpis["false_positive_rate"]["value"] == pytest.approx(fp / (tp + fp))


def test_latency_improves_after_the_optimization_event(sign_in: Any) -> None:
    body = (
        sign_in("platform")
        .get("/api/v1/analytics/latency", params={"range": "all", "granularity": "month"})
        .json()
    )
    items = {i["period"]: i for i in body["items"]}
    assert items["2026-10-01"]["p50_ms"] < items["2025-10-01"]["p50_ms"] * 0.7
    assert all(i["p95_ms"] > i["p50_ms"] for i in body["items"])


def test_operational_comparison_and_ranges(sign_in: Any) -> None:
    client = sign_in("platform")
    body = client.get("/api/v1/analytics/kpis", params={"range": "90d", "compare": "true"}).json()
    w = body["window"]
    assert w["from"] == "2026-08-03" and w["to"] == "2026-10-31"
    assert w["compare_to"] == "2026-08-02" and w["compare_from"] == "2026-05-05"
    s = body["kpis"]["screenings"]
    assert s["change"] == pytest.approx(s["value"] - s["previous"])
    bad = client.get("/api/v1/analytics/kpis", params={"range": "custom", "from": "2026-05-01"})
    assert bad.status_code == 400


def test_operational_series_with_live_rows(sign_in: Any, biz: dict[str, Any]) -> None:
    owner, t1 = biz["owner"], biz["tenants"][0]
    owner.execute("DELETE FROM mentions WHERE surface LIKE 'TEST-%'")
    owner.execute("DELETE FROM articles WHERE url LIKE 'https://test.example/%'")
    now = dt.datetime.now(dt.UTC)
    sid = owner.execute(
        "INSERT INTO subjects (tenant_id, kind, name, name_norm, country) VALUES "
        "(%s, 'person', 'Ops Subject', 'ops subject', 'GB') RETURNING id",
        (t1,),
    ).fetchone()[0]
    subject_ids = [sid]
    alerts = []
    for number, (age_hours, severity, status) in enumerate(
        (
            (2, "high", "new"),
            (50, "critical", "in_review"),
            (24 * 5, "low", "escalated"),
            (24 * 40, "medium", "new"),
            (24 * 2, "high", "false_positive"),
        )
    ):
        if number:
            subject_ids.append(
                owner.execute(
                    "INSERT INTO subjects (tenant_id, kind, name, name_norm, country) VALUES "
                    "(%s, 'person', %s, %s, 'GB') RETURNING id",
                    (t1, f"Ops Subject {number}", f"ops subject {number}"),
                ).fetchone()[0]
            )
        alerts.append(
            owner.execute(
                "INSERT INTO alerts (tenant_id, subject_id, severity, status, created_at, "
                "resolved_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    t1,
                    subject_ids[number],
                    severity,
                    status,
                    now - dt.timedelta(hours=age_hours),
                    now if status == "false_positive" else None,
                ),
            ).fetchone()[0]
        )
    article = owner.execute(
        "INSERT INTO articles (url, language, published_at) VALUES "
        "('https://test.example/1', 'de', now()) RETURNING id"
    ).fetchone()[0]
    mention = owner.execute(
        "INSERT INTO mentions (article_id, start_char, end_char, surface, route, "
        "decision) VALUES (%s, 0, 5, 'TEST-A', 'blink', 'link') RETURNING id",
        (article,),
    ).fetchone()[0]
    owner.execute(
        "INSERT INTO adverse_findings (mention_id, category, severity, evidence_sentence, "
        "prompt_version) VALUES (%s, 'fraud', 3, 'x', 'v1')",
        (mention,),
    )
    try:
        from atlas.api import ops_analytics as ops

        client = sign_in("mgr1")
        biz["app"].state.atlas.extras["cache"].clear()
        queue = client.get("/api/v1/analytics/queue-ageing").json()
        assert queue["open_alerts"] == 4 and queue["sla_hours"] == 72
        assert queue["sla_breaches"] == 2  # five days and forty days
        assert {b["bucket"]: b["alerts"] for b in queue["buckets"]} == {
            "under_1d": 1,
            "1_3d": 1,
            "3_7d": 1,
            "7_14d": 0,
            "14_30d": 0,
            "over_30d": 1,
        }
        assert queue["demo_data"] is False
        risk = client.get("/api/v1/analytics/risk-by-country", params={"range": "all"}).json()[
            "items"
        ]
        gb = next(r for r in risk if r["country"] == "GB")
        assert gb["alerts"] == 5 and gb["weighted_alerts"] == 3 + 4 + 1 + 2 + 3
        quality = client.get("/api/v1/analytics/quality", params={"range": "30d"}).json()["items"]
        assert quality and quality[-1]["false_positive"] == 1 and quality[-1]["precision"] == 0.0
        languages = client.get("/api/v1/analytics/languages", params={"range": "30d"}).json()[
            "items"
        ]
        assert {"language": "de", "mentions": 1} in languages
        categories = client.get("/api/v1/analytics/categories", params={"range": "30d"}).json()[
            "items"
        ]
        assert any(c["category"] == "fraud" and c["severity"] == 3 for c in categories)
        routing = client.get("/api/v1/analytics/routing").json()
        assert (
            routing["total"] >= 1
            and {"id": "blink", "label": "Adjudicated by BLINK"} in routing["nodes"]
        )
        assert all(link["value"] > 0 for link in routing["links"])
        from psycopg.rows import dict_row

        with psycopg.connect(biz["url"], row_factory=dict_row) as plain:
            frozen = ops.queue_ageing(plain, 24, now)
        assert frozen["sla_breaches"] == 3 and frozen["open_alerts"] == 4
    finally:
        owner.execute("DELETE FROM alert_events WHERE alert_id = ANY(%s)", (alerts,))
        owner.execute("DELETE FROM alerts WHERE id = ANY(%s)", (alerts,))
        owner.execute("DELETE FROM subjects WHERE id = ANY(%s)", (subject_ids,))
        owner.execute("DELETE FROM mentions WHERE id = %s", (mention,))
        owner.execute("DELETE FROM articles WHERE id = %s", (article,))


def test_throughput_per_analyst(sign_in: Any, biz: dict[str, Any]) -> None:
    owner, t1 = biz["owner"], biz["tenants"][0]
    sid = owner.execute(
        "INSERT INTO subjects (tenant_id, kind, name, name_norm) VALUES "
        "(%s, 'person', 'Throughput Subject', 'throughput subject') RETURNING id",
        (t1,),
    ).fetchone()[0]
    aid = owner.execute(
        "INSERT INTO alerts (tenant_id, subject_id, severity) VALUES (%s, %s, 'low') RETURNING id",
        (t1, sid),
    ).fetchone()[0]
    owner.execute(
        "INSERT INTO alert_events (alert_id, actor_id, event_type, from_status, to_status, "
        "reason) VALUES (%s, %s, 'status_change', 'new', 'false_positive', 'checked')",
        (aid, biz["users"]["mgr1"]),
    )
    try:
        biz["app"].state.atlas.extras["cache"].clear()
        rows = (
            sign_in("mgr1")
            .get("/api/v1/analytics/throughput", params={"range": "30d"})
            .json()["items"]
        )
        assert rows and rows[-1]["analyst"] == "mgr1@example.org" and rows[-1]["resolved"] >= 1
    finally:
        owner.execute("DELETE FROM alert_events WHERE alert_id = %s", (aid,))
        owner.execute("DELETE FROM alerts WHERE id = %s", (aid,))
        owner.execute("DELETE FROM subjects WHERE id = %s", (sid,))


def test_hybrid_versus_baseline_reports_missing_evaluation(sign_in: Any) -> None:
    body = sign_in("mgr1").get("/api/v1/analytics/hybrid-vs-baseline").json()
    results = Path(__file__).resolve().parents[2] / "docs" / "eval" / "results.json"
    assert body["available"] is results.exists()
    if not body["available"]:
        assert "evaluation" in body["reason"]


def test_operational_authorization_and_tenant_cache_isolation(
    sign_in: Any, biz: dict[str, Any]
) -> None:
    biz["app"].state.atlas.extras["cache"].clear()
    for role, expected in (
        ("mgr1", 200),
        ("tadmin1", 200),
        ("platform", 200),
        ("viewer1", 403),
        ("finance", 403),
    ):
        assert sign_in(role).get("/api/v1/analytics/kpis").status_code == expected, role
    assert sign_in().get("/api/v1/analytics/kpis").status_code == 401
    one, two = sign_in("mgr1"), sign_in("mgr2")
    a = one.get("/api/v1/analytics/volume", params={"range": "all", "granularity": "month"})
    b = two.get("/api/v1/analytics/volume", params={"range": "all", "granularity": "month"})
    assert a.headers["x-cache"] == "miss" and b.headers["x-cache"] == "miss"  # keyed by tenant
    assert a.json()["items"] != b.json()["items"]
    again = one.get("/api/v1/analytics/volume", params={"range": "all", "granularity": "month"})
    assert again.headers["x-cache"] == "hit" and again.json() == a.json()


def test_operational_csv_and_speed(sign_in: Any, biz: dict[str, Any]) -> None:
    client = sign_in("platform")
    reply = client.get(
        "/api/v1/analytics/volume", params={"range": "all", "granularity": "week", "format": "csv"}
    )
    assert (
        reply.status_code == 200
        and reply.text.splitlines()[0] == "period,screenings,batch_rows,api_calls"
    )
    cache = biz["app"].state.atlas.extras["cache"]
    for path in (
        "kpis",
        "volume",
        "severity",
        "quality",
        "latency",
        "languages",
        "categories",
        "risk-by-country",
        "queue-ageing",
        "throughput",
        "routing",
    ):
        cache.clear()
        started = time.perf_counter()
        ok = client.get(f"/api/v1/analytics/{path}", params={"range": "all", "granularity": "day"})
        elapsed = (time.perf_counter() - started) * 1000
        assert ok.status_code == 200 and elapsed < 500, (path, elapsed)


def test_nightly_refresh_job_runs_as_the_worker_role(biz: dict[str, Any]) -> None:
    from dataclasses import replace

    from atlas.api.app import build_state
    from atlas.api.revenue import refresh_business_views

    app_state = biz["app"].state.atlas
    worker_url = biz["url"].replace("://atlas@", "://atlas_worker@")
    state = build_state(replace(app_state.settings, database_url=worker_url))
    try:
        before = q(biz["owner"], "SELECT refreshed_at FROM view_refresh_log")[0][0]
        stamp = refresh_business_views(state)
        assert stamp > before
        assert q(biz["owner"], "SELECT refreshed_at FROM view_refresh_log")[0][0] == stamp
    finally:
        state.db.close()
    from atlas.jobs.celery_app import app as celery

    entry = celery.conf.beat_schedule["business-views-nightly"]
    assert entry["task"] == "atlas.refresh_business_views"
    assert str(entry["schedule"]).startswith("<crontab: 0 2 * * *")
    assert celery.conf.task_routes["atlas.refresh_business_views"] == {"queue": "reports"}
