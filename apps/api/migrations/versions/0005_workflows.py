"""Workflow tables: batch rows, alert feedback, monitoring runs, case entities, co-occurrence,
reports, and the worker role.

The worker role inherits the API role and adds write access to the ingestion tables and the
aggregate statistics table. It still runs under Row Level Security: tenant tables are reached
through the same ``app.tenant_id`` and ``app.platform`` settings as in the API.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UP = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_worker') THEN
        CREATE ROLE atlas_worker LOGIN;
    END IF;
END $$;
GRANT atlas_app TO atlas_worker;

ALTER TABLE screenings ADD COLUMN score_breakdown jsonb;
ALTER TABLE screening_batches ADD COLUMN source text NOT NULL DEFAULT 'json';
ALTER TABLE screening_batches ADD COLUMN failed integer NOT NULL DEFAULT 0;
ALTER TABLE screening_batches ADD COLUMN filename text;
ALTER TABLE alerts ADD COLUMN screening_id uuid REFERENCES screenings (id);
ALTER TABLE alerts ADD COLUMN score numeric(5, 2);
ALTER TABLE alerts ADD COLUMN score_breakdown jsonb;
ALTER TABLE alerts ADD COLUMN mention_id bigint REFERENCES mentions (id);
CREATE UNIQUE INDEX ux_alerts_dedup ON alerts
    (tenant_id, subject_id, coalesce(article_id, 0), coalesce(entry_id, 0));

-- Rows of an uploaded batch. The input is kept encrypted only until the row is processed.
CREATE INDEX ix_mentions_surface_trgm ON mentions USING gin (lower(surface) gin_trgm_ops);

CREATE TABLE batch_rows (
    id            bigserial PRIMARY KEY,
    batch_id      uuid NOT NULL REFERENCES screening_batches (id) ON DELETE CASCADE,
    row_no        integer NOT NULL,
    payload_enc   bytea,
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
    error         text,
    screening_id  uuid REFERENCES screenings (id),
    UNIQUE (batch_id, row_no)
);

CREATE TABLE alert_feedback (
    id           bigserial PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES tenants (id),
    alert_id     uuid NOT NULL REFERENCES alerts (id) ON DELETE CASCADE,
    label        text NOT NULL CHECK (label IN ('true_match', 'false_positive')),
    score        numeric(5, 2),
    features     jsonb NOT NULL DEFAULT '{}',
    reviewer_id  uuid REFERENCES users (id),
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_alert_feedback_tenant ON alert_feedback (tenant_id, created_at);

CREATE TABLE monitoring_runs (
    id               bigserial PRIMARY KEY,
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    last_mention_id  bigint NOT NULL DEFAULT 0,
    last_entry_id    bigint NOT NULL DEFAULT 0,
    subjects         integer NOT NULL DEFAULT 0,
    alerts_created   integer NOT NULL DEFAULT 0
);

CREATE TABLE case_entities (
    case_id    uuid NOT NULL REFERENCES cases (id) ON DELETE CASCADE,
    entity_id  bigint NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    PRIMARY KEY (case_id, entity_id)
);

CREATE TABLE entity_cooccurrence (
    entity_a    bigint NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    entity_b    bigint NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    article_id  bigint NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
    sentences   integer NOT NULL CHECK (sentences > 0),
    PRIMARY KEY (entity_a, entity_b, article_id),
    CHECK (entity_a < entity_b)
);
CREATE INDEX ix_cooccurrence_b ON entity_cooccurrence (entity_b);

CREATE TABLE reports (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants (id),
    screening_id  uuid NOT NULL REFERENCES screenings (id),
    created_by    uuid REFERENCES users (id),
    sha256        text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE alert_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE reports ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON alert_feedback USING (tenant_id = app_tenant() OR app_platform())
    WITH CHECK (tenant_id = app_tenant() OR app_platform());
CREATE POLICY tenant_scope ON reports USING (tenant_id = app_tenant() OR app_platform())
    WITH CHECK (tenant_id = app_tenant() OR app_platform());
ALTER TABLE batch_rows ENABLE ROW LEVEL SECURITY;
CREATE POLICY parent_scope ON batch_rows
    USING (EXISTS (SELECT 1 FROM screening_batches b WHERE b.id = batch_rows.batch_id))
    WITH CHECK (EXISTS (SELECT 1 FROM screening_batches b WHERE b.id = batch_rows.batch_id));
ALTER TABLE case_entities ENABLE ROW LEVEL SECURITY;
CREATE POLICY parent_scope ON case_entities
    USING (EXISTS (SELECT 1 FROM cases c WHERE c.id = case_entities.case_id))
    WITH CHECK (EXISTS (SELECT 1 FROM cases c WHERE c.id = case_entities.case_id));

GRANT SELECT ON ALL TABLES IN SCHEMA public TO atlas_app;
GRANT INSERT, UPDATE ON batch_rows, reports TO atlas_app;
GRANT INSERT ON alert_feedback TO atlas_app;
GRANT INSERT, DELETE ON case_entities TO atlas_app;
GRANT INSERT, UPDATE ON alerts TO atlas_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO atlas_app;
GRANT INSERT, UPDATE, DELETE ON articles, mentions, mention_candidates, adverse_findings,
    entity_cooccurrence, monitoring_runs, alert_stats_daily TO atlas_worker;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO atlas_worker;
"""

DOWN = """
DROP TABLE IF EXISTS reports, entity_cooccurrence, case_entities, monitoring_runs,
    alert_feedback, batch_rows CASCADE;
DROP INDEX IF EXISTS ux_alerts_dedup;
ALTER TABLE alerts DROP COLUMN IF EXISTS screening_id, DROP COLUMN IF EXISTS score,
    DROP COLUMN IF EXISTS score_breakdown, DROP COLUMN IF EXISTS mention_id;
ALTER TABLE screening_batches DROP COLUMN IF EXISTS source, DROP COLUMN IF EXISTS failed,
    DROP COLUMN IF EXISTS filename;
ALTER TABLE screenings DROP COLUMN IF EXISTS score_breakdown;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
