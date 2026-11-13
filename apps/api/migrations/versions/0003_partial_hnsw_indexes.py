"""Split the HNSW index into watchlist and knowledge-base partial indexes.

Filtering an approximate index after the scan can return fewer than k rows. Separate partial
indexes keep recall stable: a query must repeat the exact predicate of the index it targets
(see atlas.embeddings.WATCHLIST_PREDICATE and KB_PREDICATE).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_entity_embeddings_hnsw")
    op.execute(
        "CREATE INDEX ix_entity_embeddings_hnsw_watchlist ON entity_embeddings "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64) "
        "WHERE ref_type IN ('watchlist_entry', 'watchlist_alias')"
    )
    op.execute(
        "CREATE INDEX ix_entity_embeddings_hnsw_kb ON entity_embeddings "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64) "
        "WHERE ref_type IN ('entity', 'entity_alias')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_entity_embeddings_hnsw_watchlist")
    op.execute("DROP INDEX IF EXISTS ix_entity_embeddings_hnsw_kb")
    op.execute(
        "CREATE INDEX ix_entity_embeddings_hnsw ON entity_embeddings "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
