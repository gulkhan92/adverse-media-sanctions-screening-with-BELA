"""Tenancy and security, screening workflow, and business tables with materialized views.

Row Level Security policies are added in the API phase. Personal identifiers of
subjects are stored encrypted with pgcrypto (pgp_sym_encrypt) in bytea columns.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UP = """
-- Tenancy and security ------------------------------------------------------
CREATE TABLE tenants (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                text NOT NULL,
    industry            text,
    region              text,
    country             text,
    acquisition_channel text,
    is_demo             boolean NOT NULL DEFAULT true,
    created_at          timestamptz NOT NULL DEFAULT now(),
    churned_at          timestamptz
);
CREATE TABLE users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid REFERENCES tenants (id),
    email          text NOT NULL UNIQUE,
    password_hash  text NOT NULL,
    role           text NOT NULL CHECK (role IN (
        'viewer', 'analyst', 'manager', 'tenant_admin', 'finance_analyst', 'platform_admin')),
    is_active      boolean NOT NULL DEFAULT true,
    failed_logins  integer NOT NULL DEFAULT 0,
    locked_until   timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_users_tenant ON users (tenant_id);
CREATE TABLE user_mfa (
    user_id          uuid PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    totp_secret_enc  bytea NOT NULL,
    recovery_hashes  text[] NOT NULL DEFAULT '{}',
    enabled_at       timestamptz
);
CREATE TABLE refresh_tokens (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    family_id    uuid NOT NULL,
    token_hash   text NOT NULL UNIQUE,
    issued_at    timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    used_at      timestamptz,
    revoked_at   timestamptz
);
CREATE INDEX ix_refresh_tokens_family ON refresh_tokens (family_id);
CREATE TABLE api_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants (id),
    prefix        text NOT NULL UNIQUE,
    secret_hash   text NOT NULL,
    scopes        text[] NOT NULL DEFAULT '{}',
    expires_at    timestamptz,
    last_used_at  timestamptz,
    revoked_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE audit_log (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid,
    actor_id    uuid,
    action      text NOT NULL,
    target      text,
    detail      jsonb NOT NULL DEFAULT '{}',
    request_id  text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    prev_hash   text,
    row_hash    text NOT NULL
);
CREATE INDEX ix_audit_log_tenant_time ON audit_log (tenant_id, created_at DESC);

-- Screening ----------------------------------------------------------------
CREATE TABLE subjects (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenants (id),
    kind           text NOT NULL CHECK (kind IN ('person', 'organization')),
    name           text NOT NULL,
    name_norm      text NOT NULL,
    aliases        text[] NOT NULL DEFAULT '{}',
    country        text,
    dob_enc        bytea,
    identifiers_enc bytea,
    monitored      boolean NOT NULL DEFAULT false,
    deleted_at     timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_subjects_tenant ON subjects (tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_subjects_name_norm_trgm ON subjects USING gin (name_norm gin_trgm_ops);

CREATE TABLE screenings (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants (id),
    subject_id    uuid NOT NULL REFERENCES subjects (id),
    requested_by  uuid REFERENCES users (id),
    risk_score    numeric(5, 2),
    risk_level    text CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    config_version text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_screenings_tenant_time ON screenings (tenant_id, created_at DESC);
CREATE TABLE screening_matches (
    id            bigserial PRIMARY KEY,
    screening_id  uuid NOT NULL REFERENCES screenings (id) ON DELETE CASCADE,
    match_type    text NOT NULL CHECK (match_type IN ('watchlist', 'adverse_media')),
    entry_id      bigint REFERENCES watchlist_entries (id),
    mention_id    bigint REFERENCES mentions (id),
    confidence    real NOT NULL,
    features      jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_screening_matches_screening ON screening_matches (screening_id);

CREATE TABLE alerts (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants (id),
    subject_id   uuid NOT NULL REFERENCES subjects (id),
    entry_id     bigint REFERENCES watchlist_entries (id),
    article_id   bigint REFERENCES articles (id),
    severity     text NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status       text NOT NULL DEFAULT 'new' CHECK (status IN (
        'new', 'in_review', 'escalated', 'true_match', 'false_positive', 'closed')),
    assignee_id  uuid REFERENCES users (id),
    narrative    text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    resolved_at  timestamptz,
    UNIQUE (tenant_id, subject_id, article_id, entry_id)
);
CREATE INDEX ix_alerts_queue ON alerts (tenant_id, status, severity, created_at DESC);
CREATE TABLE alert_events (
    id          bigserial PRIMARY KEY,
    alert_id    uuid NOT NULL REFERENCES alerts (id) ON DELETE CASCADE,
    actor_id    uuid REFERENCES users (id),
    event_type  text NOT NULL,
    from_status text,
    to_status   text,
    reason      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_alert_events_alert ON alert_events (alert_id, created_at);
CREATE TABLE cases (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants (id),
    title       text NOT NULL,
    status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'closed')),
    owner_id    uuid REFERENCES users (id),
    due_date    date,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE case_alerts (
    case_id   uuid NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    alert_id  uuid NOT NULL REFERENCES alerts (id) ON DELETE CASCADE,
    PRIMARY KEY (case_id, alert_id)
);
CREATE TABLE case_notes (
    id          bigserial PRIMARY KEY,
    case_id     uuid NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    author_id   uuid REFERENCES users (id),
    body        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Business (synthetic, demo data only) ----------------------------------------
CREATE TABLE plans (
    id               serial PRIMARY KEY,
    code             text NOT NULL UNIQUE,
    name             text NOT NULL,
    monthly_price    numeric(12, 2) NOT NULL,
    included_screenings integer NOT NULL
);
CREATE TABLE subscriptions (
    id             bigserial PRIMARY KEY,
    tenant_id      uuid NOT NULL REFERENCES tenants (id),
    plan_id        integer NOT NULL REFERENCES plans (id),
    billing_cycle  text NOT NULL CHECK (billing_cycle IN ('monthly', 'annual')),
    price          numeric(12, 2) NOT NULL CHECK (price >= 0),
    start_date     date NOT NULL,
    end_date       date,
    change_reason  text
);
CREATE INDEX ix_subscriptions_tenant ON subscriptions (tenant_id, start_date);
CREATE TABLE invoices (
    id          bigserial PRIMARY KEY,
    tenant_id   uuid NOT NULL REFERENCES tenants (id),
    period      date NOT NULL,
    status      text NOT NULL CHECK (status IN ('paid', 'open', 'overdue')),
    due_date    date,
    total       numeric(12, 2) NOT NULL
);
CREATE INDEX ix_invoices_tenant_period ON invoices (tenant_id, period);
CREATE TABLE invoice_lines (
    id          bigserial PRIMARY KEY,
    invoice_id  bigint NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    line_type   text NOT NULL CHECK (line_type IN ('subscription', 'overage', 'credit')),
    description text,
    amount      numeric(12, 2) NOT NULL
);
CREATE TABLE usage_daily (
    tenant_id          uuid NOT NULL REFERENCES tenants (id),
    day                date NOT NULL,
    screenings         integer NOT NULL DEFAULT 0,
    batch_rows         integer NOT NULL DEFAULT 0,
    api_calls          integer NOT NULL DEFAULT 0,
    articles_matched   integer NOT NULL DEFAULT 0,
    alerts_by_severity jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, day)
);
CREATE TABLE alert_stats_daily (
    day                 date PRIMARY KEY,
    alerts              integer NOT NULL,
    confirmed_true      integer NOT NULL,
    false_positive      integer NOT NULL,
    median_review_minutes numeric(8, 2),
    p50_latency_ms      integer,
    p95_latency_ms      integer
);
CREATE TABLE marketing_spend (
    month    date NOT NULL,
    channel  text NOT NULL,
    spend    numeric(12, 2) NOT NULL,
    PRIMARY KEY (month, channel)
);
CREATE TABLE business_events (
    id     serial PRIMARY KEY,
    day    date NOT NULL,
    label  text NOT NULL
);

-- Monthly recurring revenue: monthly-equivalent price of subscriptions active at month end.
CREATE MATERIALIZED VIEW mrr_monthly AS
SELECT m.month_end,
       count(DISTINCT s.tenant_id) AS active_customers,
       COALESCE(sum(CASE WHEN s.billing_cycle = 'annual' THEN s.price / 12 ELSE s.price END), 0)
           AS mrr
FROM (
    SELECT (date_trunc('month', d) + interval '1 month - 1 day')::date AS month_end
    FROM generate_series('2023-11-01'::date, '2026-10-01'::date, interval '1 month') AS d
) m
LEFT JOIN subscriptions s
       ON s.start_date <= m.month_end AND (s.end_date IS NULL OR s.end_date > m.month_end)
GROUP BY m.month_end;
CREATE UNIQUE INDEX ux_mrr_monthly ON mrr_monthly (month_end);

-- Revenue retention by signup cohort (share of the cohort's first-month MRR still billed).
CREATE MATERIALIZED VIEW cohort_retention AS
WITH first_sub AS (
    SELECT tenant_id, date_trunc('month', min(start_date))::date AS cohort
    FROM subscriptions GROUP BY tenant_id
), monthly AS (
    SELECT s.tenant_id, f.cohort, m.month,
           sum(CASE WHEN s.billing_cycle = 'annual' THEN s.price / 12 ELSE s.price END) AS mrr
    FROM subscriptions s
    JOIN first_sub f USING (tenant_id)
    CROSS JOIN LATERAL (
        SELECT d::date AS month
        FROM generate_series(date_trunc('month', s.start_date),
                             date_trunc('month', COALESCE(s.end_date, '2026-10-01'::date)),
                             interval '1 month') AS d
    ) m
    GROUP BY s.tenant_id, f.cohort, m.month
), cohort_mrr AS (
    SELECT cohort, month,
           (extract(year FROM age(month, cohort)) * 12 + extract(month FROM age(month, cohort)))
               ::int AS months_since,
           sum(mrr) AS mrr
    FROM monthly GROUP BY cohort, month
)
SELECT c.cohort, c.months_since, c.mrr,
       c.mrr / NULLIF(first_value(c.mrr) OVER (PARTITION BY c.cohort ORDER BY c.months_since), 0)
           AS retention
FROM cohort_mrr c;
"""

DOWN = """
DROP MATERIALIZED VIEW IF EXISTS cohort_retention, mrr_monthly;
DROP TABLE IF EXISTS business_events, marketing_spend, alert_stats_daily, usage_daily,
    invoice_lines, invoices, subscriptions, plans, case_notes, case_alerts, cases, alert_events,
    alerts, screening_matches, screenings, subjects, audit_log, api_keys, refresh_tokens,
    user_mfa, users, tenants CASCADE;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
