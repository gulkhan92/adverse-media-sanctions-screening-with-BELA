# Project status

Updated 2026-11-18 (phase 7 added). Phases follow PLAN.md section 6.

| Phase | State | Notes |
|---|---|---|
| 1. Data acquisition | Complete | Scripts, dataset registry, manifest hashes |
| 2. Database, ETL, embeddings | Complete except Wikidata subset | Wikidata subset is partial (see `docs/data_quality_report.md`) |
| 3. Entity linking engine | Code complete, evaluation not run | `make eval` stages blink, llm, timing, analyze have not run; `docs/eval/report.md`, `docs/model_card.md`, `public/metrics.json`, and the 10,000 passage throughput test are open |
| 4. Backend API and security | Complete except ZAP scan | OWASP ZAP baseline needs a container runtime (see `docs/security.md`) |
| 5. Screening workflows | Complete | Score, batch CSV, monitoring, graph, reports (ADR-006) |
| 6. Business data and analytics backend | Complete | Synthetic data, revenue views, metrics (`docs/metrics.md`), forecast, operational analytics, caching, CSV export (ADR-007) |
| 7. Frontend foundation and marketing website | Complete except photographs and live screenshots | Design system, 27 marketing routes in 5 interface languages, six articles, legal text, login with MFA, SEO (ADR-008, `docs/frontend_quality.md`). Photographs and Playwright screenshots of the application are open |
| 8 to 10 | Not started | Product application, hardening, packaging |

## Verification

- 200 Python tests pass and 3 are skipped (they need a Valkey server); 7 web tests pass. Line coverage of the API and jobs packages is 91 percent (`make api-test`).
- `ruff`, `mypy`, and `bandit` report no findings on the API and jobs packages.
- Single screening latency against 19,563 watchlist entries: p50 5 ms, p95 10 ms.

## Known gaps

- No photographs are included: the stock photo sources need an API key or licensed download, so imagery is in-house SVG (the optimization pipeline is ready, see `apps/web/README.md`).
- Product visuals on the marketing site are illustrative renderings, not screenshots, until the application exists (phase 8).
- Page copy is English only; the interface chrome is translated and has not had a native review.
- The web pages send no Content Security Policy yet.

- Retention purge and hard delete of soft-deleted subjects are not implemented.
- Score weights are the plan defaults and are not fitted to reviewer outcomes.
- Article ingestion is a library function; the NLP pipeline is not yet wired to it.
- The hybrid versus baseline analytics series stays empty until the Phase 3 evaluation writes `docs/eval/results.json`.
- No hand-labeled gold evaluation set exists, so adverse media metrics are not measured.
