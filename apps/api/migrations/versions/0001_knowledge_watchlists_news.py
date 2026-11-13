"""Extensions, knowledge base, watchlists, ICIJ schema, and news tables.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UP = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Knowledge ---------------------------------------------------------------
CREATE TABLE entities (
    id           bigserial PRIMARY KEY,
    kind         text NOT NULL CHECK (kind IN ('person', 'organization')),
    qid          text UNIQUE,
    lei          text UNIQUE,
    name         text NOT NULL,
    name_norm    text NOT NULL,
    name_key     text NOT NULL,
    description  text,
    countries    text[] NOT NULL DEFAULT '{}',
    birth_date   text,
    sitelinks    integer,
    source       text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_entities_kind ON entities (kind);
CREATE INDEX ix_entities_name_norm_trgm ON entities USING gin (name_norm gin_trgm_ops);
CREATE INDEX ix_entities_name_key_trgm ON entities USING gin (name_key gin_trgm_ops);
CREATE INDEX ix_entities_name_norm_eq ON entities (name_norm);

CREATE TABLE entity_aliases (
    id          bigserial PRIMARY KEY,
    entity_id   bigint NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    alias       text NOT NULL,
    alias_norm  text NOT NULL,
    alias_key   text NOT NULL,
    lang        text,
    script      text,
    source      text NOT NULL,
    is_primary  boolean NOT NULL DEFAULT false,
    UNIQUE (entity_id, alias_norm, lang)
);
CREATE INDEX ix_entity_aliases_entity ON entity_aliases (entity_id);
CREATE INDEX ix_entity_aliases_norm_eq ON entity_aliases (alias_norm);
CREATE INDEX ix_entity_aliases_norm_trgm ON entity_aliases USING gin (alias_norm gin_trgm_ops);
CREATE INDEX ix_entity_aliases_key_trgm ON entity_aliases USING gin (alias_key gin_trgm_ops);

-- One vector table for every embedded name. ref_type selects the owning table.
CREATE TABLE entity_embeddings (
    id             bigserial PRIMARY KEY,
    ref_type       text NOT NULL CHECK (
        ref_type IN ('entity', 'entity_alias', 'watchlist_entry', 'watchlist_alias')),
    ref_id         bigint NOT NULL,
    owner_id       bigint NOT NULL,
    name_norm      text NOT NULL,
    embedding      vector(1024) NOT NULL,
    model_version  text NOT NULL,
    UNIQUE (ref_type, ref_id, model_version)
);
CREATE INDEX ix_entity_embeddings_owner ON entity_embeddings (ref_type, owner_id);
-- HNSW index, created by the embedding job after bulk load (see atlas.embeddings).
-- Parameters: m=16, ef_construction=64, cosine distance.
CREATE INDEX ix_entity_embeddings_hnsw ON entity_embeddings
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE TABLE entity_relationships (
    id        bigserial PRIMARY KEY,
    src_id    bigint NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    dst_id    bigint NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    rel_type  text NOT NULL,
    source    text NOT NULL,
    valid_from date,
    valid_to   date,
    UNIQUE (src_id, dst_id, rel_type, source)
);
CREATE INDEX ix_entity_relationships_src ON entity_relationships (src_id);
CREATE INDEX ix_entity_relationships_dst ON entity_relationships (dst_id);

-- Watchlists --------------------------------------------------------------
CREATE TABLE watchlist_sources (
    id                 bigserial PRIMARY KEY,
    code               text NOT NULL UNIQUE,
    name               text NOT NULL,
    url                text,
    license            text NOT NULL,
    commercial_use     boolean NOT NULL,
    list_type          text NOT NULL CHECK (list_type IN ('sanctions', 'pep', 'other')),
    published_on       date,
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    entry_count        integer NOT NULL DEFAULT 0
);

CREATE TABLE watchlist_entries (
    id            bigserial PRIMARY KEY,
    source_id     bigint NOT NULL REFERENCES watchlist_sources (id),
    source_key    text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('person', 'organization', 'vessel', 'aircraft')),
    name          text NOT NULL,
    name_norm     text NOT NULL,
    name_key      text NOT NULL,
    programs      text[] NOT NULL DEFAULT '{}',
    countries     text[] NOT NULL DEFAULT '{}',
    birth_dates   text[] NOT NULL DEFAULT '{}',
    birth_years   integer[] NOT NULL DEFAULT '{}',
    identifiers   jsonb NOT NULL DEFAULT '[]',
    addresses     jsonb NOT NULL DEFAULT '[]',
    remarks       text,
    listed_on     date,
    UNIQUE (source_id, source_key)
);
CREATE INDEX ix_watchlist_entries_kind ON watchlist_entries (kind);
CREATE INDEX ix_watchlist_entries_name_norm_trgm
    ON watchlist_entries USING gin (name_norm gin_trgm_ops);
CREATE INDEX ix_watchlist_entries_name_key_trgm
    ON watchlist_entries USING gin (name_key gin_trgm_ops);
CREATE INDEX ix_watchlist_entries_identifiers ON watchlist_entries USING gin (identifiers);

CREATE TABLE watchlist_aliases (
    id          bigserial PRIMARY KEY,
    entry_id    bigint NOT NULL REFERENCES watchlist_entries (id) ON DELETE CASCADE,
    alias       text NOT NULL,
    alias_norm  text NOT NULL,
    alias_key   text NOT NULL,
    alias_type  text NOT NULL,
    low_quality boolean NOT NULL DEFAULT false,
    script      text,
    UNIQUE (entry_id, alias_norm)
);
CREATE INDEX ix_watchlist_aliases_entry ON watchlist_aliases (entry_id);
CREATE INDEX ix_watchlist_aliases_norm_eq ON watchlist_aliases (alias_norm);
CREATE INDEX ix_watchlist_aliases_norm_trgm
    ON watchlist_aliases USING gin (alias_norm gin_trgm_ops);
CREATE INDEX ix_watchlist_aliases_key_trgm
    ON watchlist_aliases USING gin (alias_key gin_trgm_ops);

-- Cross-source links between a watchlist entry and a knowledge-base entity.
CREATE TABLE watchlist_entity_links (
    id          bigserial PRIMARY KEY,
    entry_id    bigint NOT NULL REFERENCES watchlist_entries (id) ON DELETE CASCADE,
    entity_id   bigint NOT NULL REFERENCES entities (id) ON DELETE CASCADE,
    method      text NOT NULL CHECK (method IN ('identifier', 'name_country', 'name', 'embedding')),
    score       real NOT NULL,
    status      text NOT NULL CHECK (status IN ('accepted', 'review', 'rejected')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entry_id, entity_id)
);
CREATE INDEX ix_wel_entity ON watchlist_entity_links (entity_id);
CREATE INDEX ix_wel_status ON watchlist_entity_links (status, method);

-- ICIJ Offshore Leaks (separate schema, ODbL database and CC BY-SA contents) --
CREATE SCHEMA icij;
CREATE TABLE icij.nodes (
    node_id        bigint PRIMARY KEY,
    node_type      text NOT NULL CHECK (
        node_type IN ('entity', 'officer', 'intermediary', 'address', 'other')),
    name           text,
    name_norm      text,
    countries      text,
    country_codes  text,
    jurisdiction   text,
    company_type   text,
    status         text,
    source_id      text,
    incorporation_date text,
    note           text
);
CREATE INDEX ix_icij_nodes_type ON icij.nodes (node_type);
CREATE INDEX ix_icij_nodes_name_norm_trgm ON icij.nodes USING gin (name_norm gin_trgm_ops);
CREATE TABLE icij.relationships (
    id          bigserial PRIMARY KEY,
    src_id      bigint NOT NULL,
    dst_id      bigint NOT NULL,
    rel_type    text,
    link        text,
    status      text,
    start_date  text,
    end_date    text,
    source_id   text
);
CREATE INDEX ix_icij_rel_src ON icij.relationships (src_id);
CREATE INDEX ix_icij_rel_dst ON icij.relationships (dst_id);

-- News and NLP --------------------------------------------------------------
CREATE TABLE articles (
    id             bigserial PRIMARY KEY,
    url            text NOT NULL UNIQUE,
    title          text,
    domain         text,
    language       text,
    source_country text,
    published_at   timestamptz,
    harvested_at   timestamptz,
    topic          text,
    snippet        text CHECK (snippet IS NULL OR char_length(snippet) <= 300),
    fetch_status   text
);
CREATE INDEX ix_articles_published ON articles (published_at DESC);
CREATE INDEX ix_articles_language ON articles (language);

CREATE TABLE article_embeddings (
    article_id     bigint PRIMARY KEY REFERENCES articles (id) ON DELETE CASCADE,
    embedding      vector(1024) NOT NULL,
    model_version  text NOT NULL
);

CREATE TABLE mentions (
    id           bigserial PRIMARY KEY,
    article_id   bigint NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
    start_char   integer NOT NULL,
    end_char     integer NOT NULL,
    surface      text NOT NULL,
    qid          text,
    entity_id    bigint REFERENCES entities (id),
    md_score     real,
    el_score     real,
    route        text CHECK (route IN ('accept', 'blink', 'llm', 'nil')),
    decision     text,
    model_version text
);
CREATE INDEX ix_mentions_article ON mentions (article_id);
CREATE INDEX ix_mentions_qid ON mentions (qid);

CREATE TABLE mention_candidates (
    id          bigserial PRIMARY KEY,
    mention_id  bigint NOT NULL REFERENCES mentions (id) ON DELETE CASCADE,
    rank        smallint NOT NULL,
    qid         text NOT NULL,
    bela_score  real,
    blink_score real,
    llm_label   text
);
CREATE INDEX ix_mention_candidates_mention ON mention_candidates (mention_id);

CREATE TABLE adverse_findings (
    id               bigserial PRIMARY KEY,
    mention_id       bigint NOT NULL REFERENCES mentions (id) ON DELETE CASCADE,
    category         text NOT NULL,
    severity         smallint NOT NULL CHECK (severity BETWEEN 1 AND 4),
    evidence_sentence text NOT NULL,
    prompt_version   text NOT NULL,
    model_version    text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_adverse_findings_mention ON adverse_findings (mention_id);
"""

DOWN = """
DROP TABLE IF EXISTS adverse_findings, mention_candidates, mentions, article_embeddings,
    articles CASCADE;
DROP SCHEMA IF EXISTS icij CASCADE;
DROP TABLE IF EXISTS watchlist_entity_links, watchlist_aliases, watchlist_entries,
    watchlist_sources, entity_relationships, entity_embeddings, entity_aliases,
    entities CASCADE;
"""


def upgrade() -> None:
    op.execute(UP)


def downgrade() -> None:
    op.execute(DOWN)
