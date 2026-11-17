# ADR-006: Screening workflows, scoring, monitoring, graph, and reports

Status: accepted (phase 5)

## Context

Phase 4 delivered the API and its security model with a placeholder confidence heuristic.
Phase 5 adds the workflows a compliance team uses: a documented and configurable risk score,
asynchronous batch screening, continuous monitoring with de-duplicated alerts, reviewer
feedback, an entity graph, and a PDF report. PLAN.md section 6 (phase 5) defines the scope.

## Decisions

**Score.** `scoring.py` implements the plan formula

    score = 100 * (0.40 * list_weight * match_confidence
                 + 0.35 * adverse_severity * link_confidence
                 + 0.15 * recency_decay + 0.10 * source_reliability)

with list weights sanctions 1.0, PEP 0.6, other lists 0.5, adverse media only 0.4, and levels
critical at 80 or above, high 60 to 79, medium 40 to 59, low below 40. Severity is the finding
severity (1 to 4) divided by 4. Recency is exponential decay with a 365 day half-life, and an
active list entry counts as current. Source reliability is 1.0 for official sanctions lists, 0.9
for PEP lists, 0.8 for other lists, and 0.6 for news. Recency and reliability describe a match,
so they are zero when nothing matched; an exact list match with no adverse media therefore
scores 65 (high), and critical needs corroborating adverse media. The stored breakdown holds
every component with its weight, inputs, and points, and the tests check that the points sum to
the score.

Tenant administrators may change weights, thresholds, list weights, reliabilities, and the
half-life only inside fixed bounds (weights 0.05 to 0.60 and summing to 1, thresholds
medium 20 to 50, high 50 to 75, critical 70 to 95 and ordered, sanctions weight not below the PEP
weight). Each saved change increments a version that is recorded on every screening.

**Retrieval.** A screening first tries exact identifiers (watchlist identifiers are matched by
value), then trigram similarity over names, aliases, and the transliterated key, then vector
similarity over stored name embeddings when an embedder is configured
(`ATLAS_VECTOR_SEARCH=1` and the entity linking service). Vector hits count 0.93 of their cosine
similarity because they are a weaker signal. If the embedder is unreachable, screening continues
and the response reports `vector_search: unavailable`; a 30 second cooldown keeps the failure
from adding latency to every request. Adverse media is attached by three routes in decreasing
certainty: mentions linked to the entity of a matched list entry, mentions linked to an entity
with the subject's name, and mentions whose surface text matches the name (weighted 0.7).

**Batch screening.** Uploaded CSV files (UTF-8, at most 10,000 rows, 5 MB) are validated row by
row. Rejected rows are listed with the reason and never stop the batch. Accepted rows are stored
encrypted in `batch_rows` and wiped as each row finishes. Processing runs after the request
transaction commits: on a Celery worker when `ATLAS_CELERY_BROKER_URL` is set, otherwise in the
API process. Each row runs in its own savepoint so one failure affects only that row, and the
recorded error contains the exception type only, never row content. The task is safe to run
twice. Status reports progress, failures, and results; the export neutralises spreadsheet
formulas.

**Monitoring.** `scan_monitored` re-evaluates monitored subjects and creates an alert for each
(subject, article, list entry) whose own score reaches the tenant's medium threshold. A unique
index on `(tenant, subject, coalesce(article, 0), coalesce(entry, 0))` makes creation
idempotent, so overlapping scans cannot duplicate alerts. A run is skipped when no mention or
list entry is newer than the previous run (watermarks in `monitoring_runs`). Turning monitoring
on scans that subject immediately. New alerts are published to the tenant's event stream. The
worker connects as `atlas_worker`, which inherits the API role and additionally writes the
ingestion tables and the daily statistics table. Beat schedules a scan every 5 minutes and a
statistics refresh every 15 minutes.

**Reviewer feedback.** A `true_match` or `false_positive` decision writes a row to
`alert_feedback` with the alert score and its breakdown. A reopened and re-decided alert adds a
second row, so the history of decisions is preserved for later calibration. A job recomputes the
daily counts in `alert_stats_daily` from alert history across tenants (it updates only the alert
columns).

**Graph.** `graph_service.py` loads subgraphs breadth first into networkx from
`entity_relationships` (registry and ownership) and `entity_cooccurrence` (article evidence).
Expansion is limited to depth 3 and 500 nodes and reports truncation. Co-occurrence is computed at
ingestion, because it needs the sentence boundaries of the full text, and stores for each entity
pair and article the number of shared sentences. Edges carry provenance: relationship type and
source, or the supporting article IDs with the summed sentence weight.

**Ingestion.** `ingest_article` stores the article record with a 300 character snippet only,
its linked mentions, adverse findings, and co-occurrence. Re-ingesting an article replaces its
mentions.

**Reports.** WeasyPrint renders an A4 PDF with subject, score components, watchlist matches,
evidence excerpts with source links, reviewer decisions, the disclaimer, timestamp, and report ID.
The renderer refuses every external fetch, text is HTML-escaped, and only http and https links
are emitted. Each report is recorded with the SHA-256 of its bytes. On macOS the Homebrew
library path is added automatically; Linux needs the Pango packages.

## Consequences

- Every score can be explained from stored data, and changes to weights are versioned.
- Alert counts depend on the tenant's medium threshold and on evidence recency; an old, weak
  mention can score below it and raise no alert.
- Monitoring evaluates all monitored subjects whenever new data exists. This is acceptable for
  thousands of subjects; larger deployments need per-subject candidate pruning.
- The score weights are the plan's defaults and have not been fitted to reviewer outcomes. The
  feedback table exists so that a later phase can measure calibration.
