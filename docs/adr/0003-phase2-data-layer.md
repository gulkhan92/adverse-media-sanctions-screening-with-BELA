# ADR-003: Data layer decisions for phase 2

Status: accepted. Date: 2026-11-09.

## Context

Phase 2 loads the acquired sources into PostgreSQL with pgvector, links watchlist entries to
the knowledge base, and embeds names. Four decisions departed from the plan or filled gaps.

## Decisions

1. **Wikidata subset from public APIs, not a full dump.** The dump is about 140 GB
   compressed and the SPARQL endpoint times out on class-wide popularity queries. The loaded
   subset is (a) every item with a LEI (P1278, about 53 thousand), and (b) items returned by
   searching the Wikidata API for each OFAC primary name, kept when they are humans or have an
   organization-type property. This reaches fewer entities than the planned 500 thousand.
   `data/scripts/build_wikidata_subset.py` remains the route to the full subset when a dump is
   available, and both write the same parquet schema. Consequence: entity linking benchmarks in
   phase 3 must report candidate recall against this knowledge base, not the full one.
2. **Partial HNSW indexes.** One index per use (watchlist names, knowledge-base names) with a
   `WHERE ref_type IN (...)` predicate. A single index filtered after the scan returns fewer than
   k rows when the filter is selective. Queries must repeat the exact predicate
   (`atlas.embeddings.WATCHLIST_PREDICATE`, `KB_PREDICATE`). Parameters follow the plan
   (m=16, ef_construction=64, cosine). The embedding job drops and recreates the indexes around
   a bulk load because building once is faster than maintaining them during inserts.
3. **GLEIF selection.** Of 2.0 million active LEI records, a record is kept when its LEI is in
   Wikidata, its normalized name equals a watchlist name, or its legal address country is not a
   large domestic registry (list in `atlas.etl.gleif.LARGE_REGISTRIES`). This keeps offshore,
   high-risk, and long-tail jurisdictions, which matter most for screening, and lands near the
   planned 300 thousand rows. Only relationships whose endpoints are both kept are loaded.
4. **Name normalization.** NFKC, case folding, accent removal, joiner and punctuation removal,
   company suffix stripping for organizations, and a token-sorted ASCII transliteration key
   (`anyascii`, ISC licence; `Unidecode` was rejected because it is GPL). Persons listed as
   "LAST, First" are reordered to "First LAST" at load time.

## Consequences

- Reloading a source replaces its rows, so downstream links and embeddings are rebuilt
  (`make resolve`, `make embed`).
- Row Level Security and encrypted subject columns are defined in the schema but enforced in
  phase 4.

## Findings at the end of phase 2 (measured, see docs/data_quality_report.md)

- Retrieval, `BAAI/bge-m3`, fixed-seed query sets of 1,000: perturbed watchlist names reach
  recall@1 0.989 and recall@5 0.999 (target met); non-English Wikidata aliases reach recall@1
  0.961 and recall@5 0.979 against the knowledge base (target met).
- **Cross-script matching target not met:** non-Latin watchlist aliases against the Latin
  primary name reach recall@1 0.448 and recall@5 0.651 (targets 0.50 and 0.80, set before the
  measurement). Many pairs are translations rather than transliterations (for example a
  Russian legal-form phrase against its English rendering), which a name embedding handles
  poorly. Thresholds were not tuned to hide this. The screening phase should combine the vector
  channel with trigram search on the transliterated `name_key`, and the combined channel must be
  re-measured against these same query sets.
- The query-set builder initially counted a query's own indexed vector as a hit (cross-script
  recall 0.999). Self-hits are now excluded.
- Watchlist link coverage is 0.79 percent accepted and 2.93 percent accepted or in review. This
  reflects an incomplete Wikidata subset (about 45 percent of LEI items fetched and about 24
  percent of OFAC names searched when the jobs were stopped), not the method. OFAC legacy and
  advanced files expose almost no LEIs, so the identifier method found no links.
- Suffix stripping makes names that differ only in legal form compare equal (for example
  "DMCC" and "FZCO" with the same stem). Such links are accepted when the country agrees and the
  candidate is unique; this is a known source of false links and should be reviewed in phase 3.
- The Wikidata subset keeps any item with a LEI as an organization, which includes
  municipalities.
- A historic country code (`DD`) from Wikidata failed the ISO check and is now filtered at load.

## Resuming the Wikidata subset

`make etl-wikidata-fetch` continues from `data/processed/wikidata/*_cache.jsonl`. Afterwards run
`make etl-wikidata etl-gleif embed resolve report` in that order.
