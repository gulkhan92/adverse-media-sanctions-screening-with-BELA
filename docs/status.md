# Project status

Updated 2026-11-17. Phases follow PLAN.md section 6.

| Phase | State | Notes |
|---|---|---|
| 1. Data acquisition | Complete | Scripts, dataset registry, manifest hashes |
| 2. Database, ETL, embeddings | Complete except Wikidata subset | Wikidata subset is partial (see `docs/data_quality_report.md`) |
| 3. Entity linking engine | Code complete, evaluation not run | `make eval` stages blink, llm, timing, analyze have not run; `docs/eval/report.md`, `docs/model_card.md`, `public/metrics.json`, and the 10,000 passage throughput test are open |
| 4. Backend API and security | Complete except ZAP scan | OWASP ZAP baseline needs a container runtime (see `docs/security.md`) |
| 5. Screening workflows | Complete | Score, batch CSV, monitoring, graph, reports (ADR-006) |
| 6 to 10 | Not started | Business analytics, frontend, hardening, packaging |

## Verification

- 163 tests pass; line coverage of the API and jobs packages is 90 percent (`make api-test`).
- `ruff`, `mypy`, and `bandit` report no findings on the API and jobs packages.
- Single screening latency against 19,563 watchlist entries: p50 5 ms, p95 10 ms.

## Known gaps

- Retention purge and hard delete of soft-deleted subjects are not implemented.
- Score weights are the plan defaults and are not fitted to reviewer outcomes.
- Article ingestion is a library function; the NLP pipeline is not yet wired to it.
- No hand-labeled gold evaluation set exists, so adverse media metrics are not measured.
