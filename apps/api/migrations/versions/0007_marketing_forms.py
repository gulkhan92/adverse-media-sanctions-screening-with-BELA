"""Marketing forms: richer demo requests and a double opt-in newsletter list.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UP = """
ALTER TABLE contact_requests
    ADD COLUMN role text,
    ADD COLUMN industry text,
    ADD COLUMN consent_at timestamptz;

CREATE TABLE newsletter_subscribers (
    id            bigserial PRIMARY KEY,
    email         text NOT NULL UNIQUE,
    token_hash    text NOT NULL,
    confirmed_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE ON newsletter_subscribers TO atlas_app;
GRANT USAGE ON SEQUENCE newsletter_subscribers_id_seq TO atlas_app;
"""

DOWN = """
DROP TABLE IF EXISTS newsletter_subscribers;
ALTER TABLE contact_requests DROP COLUMN IF EXISTS role, DROP COLUMN IF EXISTS industry,
    DROP COLUMN IF EXISTS consent_at;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
