"""Business analytics views: per-tenant MRR, MRR movements, revenue by dimension, usage revenue.

MRR is the monthly-equivalent recurring fee of the subscriptions active at month end (annual
plans divided by 12). Everything else derives from ``tenant_mrr_monthly``, so the movements
reconcile to the MRR change by construction. The views are refreshed concurrently by
``refresh_business_views()`` (nightly and on demand). The function runs as the table owner
because the application role cannot refresh materialized views itself.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

MONTHS = (
    "SELECT (date_trunc('month', d) + interval '1 month - 1 day')::date AS month_end "
    "FROM generate_series('2023-11-01'::date, '2026-10-01'::date, interval '1 month') d"
)

UP = f"""
CREATE MATERIALIZED VIEW tenant_mrr_monthly AS
WITH months AS ({MONTHS}),
t AS (
    SELECT t.id, t.region, t.industry, t.acquisition_channel AS channel,
           date_trunc('month', min(s.start_date))::date AS signup_month
    FROM tenants t JOIN subscriptions s ON s.tenant_id = t.id GROUP BY t.id
)
SELECT t.id AS tenant_id, m.month_end,
       coalesce(a.mrr, 0)::numeric(14, 2) AS mrr,
       coalesce(a.plan_code, l.plan_code) AS plan_code,
       t.region, t.industry, t.channel, t.signup_month
FROM t
JOIN months m ON m.month_end >= (t.signup_month + interval '1 month - 1 day')::date
LEFT JOIN LATERAL (
    SELECT sum(CASE WHEN s.billing_cycle = 'annual' THEN s.price / 12 ELSE s.price END) AS mrr,
           max(p.code) AS plan_code
    FROM subscriptions s JOIN plans p ON p.id = s.plan_id
    WHERE s.tenant_id = t.id AND s.start_date <= m.month_end
      AND (s.end_date IS NULL OR s.end_date > m.month_end)
) a ON true
LEFT JOIN LATERAL (
    SELECT p.code AS plan_code FROM subscriptions s JOIN plans p ON p.id = s.plan_id
    WHERE s.tenant_id = t.id AND s.start_date <= m.month_end
    ORDER BY s.start_date DESC LIMIT 1
) l ON true;
CREATE UNIQUE INDEX ux_tenant_mrr_monthly ON tenant_mrr_monthly (tenant_id, month_end);
CREATE INDEX ix_tenant_mrr_monthly_month ON tenant_mrr_monthly (month_end);

CREATE MATERIALIZED VIEW tenant_mrr_movements AS
SELECT x.tenant_id, x.month_end, x.plan_code, x.region, x.industry, x.channel, x.signup_month,
       x.prev_mrr, x.mrr,
       CASE
           WHEN x.prev_mrr = 0 AND x.mrr > 0 THEN 'new'
           WHEN x.prev_mrr > 0 AND x.mrr = 0 THEN 'churn'
           WHEN x.prev_mrr > 0 AND x.mrr > x.prev_mrr THEN 'expansion'
           WHEN x.mrr > 0 AND x.mrr < x.prev_mrr THEN 'contraction'
           ELSE 'none'
       END AS movement,
       x.mrr - x.prev_mrr AS delta
FROM (
    SELECT *, coalesce(lag(mrr) OVER (PARTITION BY tenant_id ORDER BY month_end), 0) AS prev_mrr
    FROM tenant_mrr_monthly
) x;
CREATE UNIQUE INDEX ux_tenant_mrr_movements ON tenant_mrr_movements (tenant_id, month_end);
CREATE INDEX ix_tenant_mrr_movements_month ON tenant_mrr_movements (month_end);

CREATE MATERIALIZED VIEW mrr_movements AS
SELECT month_end,
       sum(prev_mrr) AS begin_mrr,
       sum(mrr) AS end_mrr,
       coalesce(sum(delta) FILTER (WHERE movement = 'new'), 0) AS new_mrr,
       coalesce(sum(delta) FILTER (WHERE movement = 'expansion'), 0) AS expansion_mrr,
       coalesce(-sum(delta) FILTER (WHERE movement = 'contraction'), 0) AS contraction_mrr,
       coalesce(-sum(delta) FILTER (WHERE movement = 'churn'), 0) AS churn_mrr,
       count(*) FILTER (WHERE movement = 'new') AS new_customers,
       count(*) FILTER (WHERE movement = 'expansion') AS expansion_customers,
       count(*) FILTER (WHERE movement = 'contraction') AS contraction_customers,
       count(*) FILTER (WHERE movement = 'churn') AS churned_customers,
       count(*) FILTER (WHERE prev_mrr > 0) AS begin_customers,
       count(*) FILTER (WHERE mrr > 0) AS end_customers
FROM tenant_mrr_movements GROUP BY month_end;
CREATE UNIQUE INDEX ux_mrr_movements ON mrr_movements (month_end);

CREATE MATERIALIZED VIEW revenue_by_dimension AS
SELECT month_end, 'plan'::text AS dimension, plan_code AS value, sum(mrr) AS mrr,
       count(*) FILTER (WHERE mrr > 0) AS customers FROM tenant_mrr_monthly GROUP BY 1, 3
UNION ALL
SELECT month_end, 'region', region, sum(mrr), count(*) FILTER (WHERE mrr > 0)
FROM tenant_mrr_monthly GROUP BY 1, 3
UNION ALL
SELECT month_end, 'industry', industry, sum(mrr), count(*) FILTER (WHERE mrr > 0)
FROM tenant_mrr_monthly GROUP BY 1, 3
UNION ALL
SELECT month_end, 'channel', channel, sum(mrr), count(*) FILTER (WHERE mrr > 0)
FROM tenant_mrr_monthly GROUP BY 1, 3;
CREATE UNIQUE INDEX ux_revenue_by_dimension ON revenue_by_dimension (month_end, dimension, value);

-- Billed amounts by invoice period. Overage is usage revenue and is not part of MRR.
CREATE MATERIALIZED VIEW usage_revenue_monthly AS
SELECT i.period AS month,
       coalesce(sum(l.amount) FILTER (WHERE l.line_type = 'subscription'), 0) AS subscription,
       coalesce(sum(l.amount) FILTER (WHERE l.line_type = 'overage'), 0) AS overage,
       coalesce(sum(l.amount) FILTER (WHERE l.line_type = 'credit'), 0) AS credit,
       coalesce(sum(l.amount), 0) AS total
FROM invoices i JOIN invoice_lines l ON l.invoice_id = i.id GROUP BY i.period;
CREATE UNIQUE INDEX ux_usage_revenue_monthly ON usage_revenue_monthly (month);

CREATE UNIQUE INDEX ux_cohort_retention ON cohort_retention (cohort, months_since);
CREATE INDEX ix_usage_daily_day ON usage_daily (day);
CREATE INDEX ix_mentions_route ON mentions (route);

CREATE TABLE view_refresh_log (
    name         text PRIMARY KEY,
    refreshed_at timestamptz NOT NULL
);

CREATE FUNCTION refresh_business_views() RETURNS timestamptz
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE stamp timestamptz := clock_timestamp();
BEGIN
    -- Dependency order: base, movements, then aggregates.
    REFRESH MATERIALIZED VIEW CONCURRENTLY mrr_monthly;
    REFRESH MATERIALIZED VIEW CONCURRENTLY cohort_retention;
    REFRESH MATERIALIZED VIEW CONCURRENTLY tenant_mrr_monthly;
    REFRESH MATERIALIZED VIEW CONCURRENTLY tenant_mrr_movements;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mrr_movements;
    REFRESH MATERIALIZED VIEW CONCURRENTLY revenue_by_dimension;
    REFRESH MATERIALIZED VIEW CONCURRENTLY usage_revenue_monthly;
    INSERT INTO view_refresh_log (name, refreshed_at) VALUES ('business', stamp)
        ON CONFLICT (name) DO UPDATE SET refreshed_at = EXCLUDED.refreshed_at;
    RETURN stamp;
END $$;

GRANT SELECT ON ALL TABLES IN SCHEMA public TO atlas_app;
GRANT EXECUTE ON FUNCTION refresh_business_views() TO atlas_app;
"""

DOWN = """
DROP FUNCTION IF EXISTS refresh_business_views();
DROP TABLE IF EXISTS view_refresh_log;
DROP INDEX IF EXISTS ux_cohort_retention, ix_usage_daily_day, ix_mentions_route;
DROP MATERIALIZED VIEW IF EXISTS usage_revenue_monthly, revenue_by_dimension, mrr_movements,
    tenant_mrr_movements, tenant_mrr_monthly CASCADE;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
