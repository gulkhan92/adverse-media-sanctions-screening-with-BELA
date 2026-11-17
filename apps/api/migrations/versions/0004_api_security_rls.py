"""API support tables, Row Level Security, application role, and the audit hash chain.

The application connects as the ``atlas_app`` role, which owns nothing, so Row Level
Security applies to it. Tenant scope is set per request with ``SET LOCAL app.tenant_id``;
platform scope (``app.platform = 'on'``) is set by the server for platform operators and for
pre-authentication lookups. Knowledge and watchlist tables are global and read-only for the
role. The audit log accepts inserts only and every row carries a hash of the previous row.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "users",
    "api_keys",
    "subjects",
    "screenings",
    "alerts",
    "cases",
    "subscriptions",
    "invoices",
    "usage_daily",
    "risk_config",
    "screening_batches",
    "idempotency_keys",
]
# child table -> (parent table, child column, parent column)
CHILD_TABLES = {
    "user_mfa": ("users", "user_id", "id"),
    "refresh_tokens": ("users", "user_id", "id"),
    "password_reset_tokens": ("users", "user_id", "id"),
    "screening_matches": ("screenings", "screening_id", "id"),
    "alert_events": ("alerts", "alert_id", "id"),
    "case_alerts": ("cases", "case_id", "id"),
    "case_notes": ("cases", "case_id", "id"),
    "invoice_lines": ("invoices", "invoice_id", "id"),
}

HEAD = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_app') THEN
        CREATE ROLE atlas_app LOGIN;
    END IF;
END $$;

CREATE TABLE password_reset_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash  text NOT NULL UNIQUE,
    expires_at  timestamptz NOT NULL,
    used_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE risk_config (
    tenant_id   uuid PRIMARY KEY REFERENCES tenants (id),
    config      jsonb NOT NULL,
    version     integer NOT NULL DEFAULT 1,
    updated_by  uuid REFERENCES users (id),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE screening_batches (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants (id),
    status        text NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    total         integer NOT NULL,
    completed     integer NOT NULL DEFAULT 0,
    requested_by  uuid REFERENCES users (id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz
);
CREATE TABLE idempotency_keys (
    tenant_id     uuid NOT NULL REFERENCES tenants (id),
    key           text NOT NULL,
    request_hash  text NOT NULL,
    status_code   integer NOT NULL,
    response      jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, key)
);
CREATE TABLE contact_requests (
    id          bigserial PRIMARY KEY,
    name        text NOT NULL,
    email       text NOT NULL,
    company     text,
    message     text NOT NULL,
    ip_hash     text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE screenings ADD COLUMN batch_id uuid REFERENCES screening_batches (id);
ALTER TABLE api_keys ADD COLUMN name text NOT NULL DEFAULT 'key';
ALTER TABLE users ADD COLUMN last_login_at timestamptz;
ALTER TABLE users ADD COLUMN full_name text;
ALTER TABLE refresh_tokens ADD COLUMN family_started_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE refresh_tokens ADD COLUMN user_agent text;
ALTER TABLE refresh_tokens ADD COLUMN ip text;
ALTER TABLE refresh_tokens ADD COLUMN last_seen_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX ix_alerts_tenant_created ON alerts (tenant_id, created_at DESC, id);
"""

FUNCTIONS = """
CREATE FUNCTION app_tenant() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;
CREATE FUNCTION app_platform() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT coalesce(current_setting('app.platform', true), '') = 'on' $$;

CREATE FUNCTION audit_row_hash(rec audit_log, prev text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(
        coalesce(prev, '') || '|' || rec.id::text || '|' || coalesce(rec.tenant_id::text, '')
        || '|' || coalesce(rec.actor_id::text, '') || '|' || rec.action || '|'
        || coalesce(rec.target, '') || '|' || rec.detail::text || '|'
        || coalesce(rec.request_id, '') || '|'
        || to_char(rec.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        'UTF8')), 'hex')
$$;

CREATE FUNCTION audit_chain() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE prev text;
BEGIN
    PERFORM pg_advisory_xact_lock(7001);
    SELECT row_hash INTO prev FROM audit_log ORDER BY id DESC LIMIT 1;
    NEW.created_at := clock_timestamp();
    NEW.prev_hash := prev;
    NEW.row_hash := audit_row_hash(NEW, prev);
    RETURN NEW;
END $$;
CREATE TRIGGER audit_chain_insert BEFORE INSERT ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_chain();

CREATE FUNCTION audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END $$;
CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_immutable();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_immutable();

-- Returns the id of the first row whose hash or link does not verify, or null when intact.
CREATE FUNCTION audit_verify() RETURNS bigint LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = public, pg_temp AS $$
    SELECT min((x.rec).id) FROM (
        SELECT a AS rec, lag(a.row_hash) OVER (ORDER BY a.id) AS expected_prev FROM audit_log a
    ) x
    WHERE (x.rec).prev_hash IS DISTINCT FROM x.expected_prev
       OR (x.rec).row_hash <> audit_row_hash(x.rec, x.expected_prev)
$$;
"""

GRANTS = """
GRANT USAGE ON SCHEMA public TO atlas_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO atlas_app;
GRANT EXECUTE ON FUNCTION app_tenant(), app_platform(), audit_verify() TO atlas_app;
GRANT INSERT, UPDATE ON users, tenants, subjects, alerts, cases, risk_config, screening_batches
    TO atlas_app;
GRANT INSERT, UPDATE, DELETE ON user_mfa, api_keys TO atlas_app;
GRANT INSERT, UPDATE ON refresh_tokens, password_reset_tokens TO atlas_app;
GRANT INSERT ON screenings, screening_matches, alert_events, case_alerts, case_notes,
    idempotency_keys, contact_requests, audit_log TO atlas_app;
GRANT UPDATE ON screenings TO atlas_app;
GRANT INSERT, UPDATE ON usage_daily TO atlas_app;
GRANT DELETE ON case_alerts TO atlas_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO atlas_app;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM atlas_app;
"""

DOWN = """
DROP FUNCTION IF EXISTS audit_verify(), audit_chain(), audit_immutable(),
    audit_row_hash(audit_log, text), app_tenant(), app_platform() CASCADE;
ALTER TABLE screenings DROP COLUMN IF EXISTS batch_id;
DROP TABLE IF EXISTS contact_requests, idempotency_keys, screening_batches, risk_config,
    password_reset_tokens CASCADE;
ALTER TABLE api_keys DROP COLUMN IF EXISTS name;
ALTER TABLE users DROP COLUMN IF EXISTS last_login_at, DROP COLUMN IF EXISTS full_name;
ALTER TABLE refresh_tokens DROP COLUMN IF EXISTS family_started_at,
    DROP COLUMN IF EXISTS user_agent, DROP COLUMN IF EXISTS ip, DROP COLUMN IF EXISTS last_seen_at;
"""


def _policies() -> str:
    parts = [
        "ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;",
        "CREATE POLICY tenant_scope ON tenants USING (id = app_tenant() OR app_platform()) "
        "WITH CHECK (id = app_tenant() OR app_platform());",
        "ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;",
        "CREATE POLICY audit_read ON audit_log FOR SELECT "
        "USING (tenant_id = app_tenant() OR app_platform());",
        "CREATE POLICY audit_write ON audit_log FOR INSERT WITH CHECK (true);",
    ]
    for table in TENANT_TABLES:
        parts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        parts.append(
            f"CREATE POLICY tenant_scope ON {table} USING (tenant_id = app_tenant() "
            f"OR app_platform()) WITH CHECK (tenant_id = app_tenant() OR app_platform());"
        )
    for child, (parent, ccol, pcol) in CHILD_TABLES.items():
        parts.append(f"ALTER TABLE {child} ENABLE ROW LEVEL SECURITY;")
        check = f"EXISTS (SELECT 1 FROM {parent} p WHERE p.{pcol} = {child}.{ccol})"
        parts.append(f"CREATE POLICY parent_scope ON {child} USING ({check}) WITH CHECK ({check});")
    return "\n".join(parts)


def upgrade() -> None:
    op.execute(HEAD)
    op.execute(FUNCTIONS)
    op.execute(_policies())
    op.execute(GRANTS)


def downgrade() -> None:
    op.execute(DOWN)
