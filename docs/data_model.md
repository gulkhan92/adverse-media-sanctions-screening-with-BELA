# Data model

PostgreSQL 16 or later with `vector`, `pg_trgm`, and `pgcrypto`. Migrations are in
`apps/api/migrations/versions/` (Alembic, written as SQL). Row Level Security policies and
column encryption are specified here and enforced in phase 4.

## Groups

| Group | Tables | Notes |
|---|---|---|
| Tenancy and security | `tenants`, `users`, `user_mfa`, `refresh_tokens`, `api_keys`, `audit_log` | `audit_log` carries `prev_hash` and `row_hash` for the hash chain |
| Knowledge | `entities`, `entity_aliases`, `entity_embeddings`, `entity_relationships` | Global, read-only for tenants. Sources: Wikidata, GLEIF |
| Watchlists | `watchlist_sources`, `watchlist_entries`, `watchlist_aliases`, `watchlist_entity_links` | OFAC SDN is the primary list. Links carry method, score, status |
| ICIJ (schema `icij`) | `icij.nodes`, `icij.relationships` | ODbL database, CC BY-SA contents. Kept apart from the knowledge base |
| News and NLP | `articles`, `article_embeddings`, `mentions`, `mention_candidates`, `adverse_findings` | Snippets of at most 300 characters (database check) |
| Screening | `subjects`, `screenings`, `screening_matches`, `alerts`, `alert_events`, `cases`, `case_alerts`, `case_notes` | `subjects.dob_enc` and `identifiers_enc` are pgcrypto ciphertext |
| Business (synthetic) | `plans`, `subscriptions`, `invoices`, `invoice_lines`, `usage_daily`, `alert_stats_daily`, `marketing_spend`, `business_events` and materialized views `mrr_monthly`, `cohort_retention` | Demo data only; populated in phase 6 |

## Name columns

Every searchable name has three forms, produced by `atlas.names.normalize_name`:

- `name` or `alias`: the original text (persons reordered from "LAST, First" to "First LAST").
- `name_norm` or `alias_norm`: NFKC, case folded, accents and punctuation removed, company
  suffixes removed for organizations. Used for exact matching. Trigram (GIN) indexed.
- `name_key` or `alias_key`: ASCII transliteration of `name_norm` with tokens sorted. Used for
  cross-script and word-order tolerant fuzzy matching. Trigram (GIN) indexed.

## Vectors

`entity_embeddings` holds one row per embedded name: `ref_type` (`entity`, `entity_alias`,
`watchlist_entry`, `watchlist_alias`), `ref_id`, `owner_id` (the entity or entry that owns the
name), the unit-length `vector(1024)` from `BAAI/bge-m3`, and `model_version`. Two partial HNSW
indexes (m=16, ef_construction=64, cosine) serve watchlist and knowledge-base searches; see
ADR-003. Embedded text is the original-script name.

## Links between sources

`watchlist_entity_links(entry_id, entity_id, method, score, status)`:

| Method | Rule | Typical status |
|---|---|---|
| `identifier` | LEI on the watchlist entry equals an entity LEI | accepted |
| `name_country` | exact normalized name or alias, with matching country or birth year | accepted if the only strong candidate, else review |
| `name` | exact normalized match without confirming attributes, or with conflicting attributes | review, or rejected on conflict |
| `embedding` | bge-m3 cosine similarity of at least 0.85 for entries not linked otherwise | review, accepted at 0.97 or more without country conflict |

## Loading order

`make db-up migrate`, then `make etl-ofac`, `make etl-wikidata`, `make etl-gleif`,
`make etl-icij`, `make etl-articles`, then `make embed`, `make resolve`, `make report`.
GLEIF must follow Wikidata because LEI-matched records merge into existing Wikidata entities.
