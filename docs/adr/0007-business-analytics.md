# ADR-007: Business data, revenue views, forecast, and analytics endpoints

Status: accepted (phase 6)

## Context

The platform dashboards need revenue and usage history for a fictional customer base, with
metric definitions that cannot drift between the database, the API, and the interface. There
are no real customers, so all business data is synthetic and must be labeled as demo data.

## Decisions

**Synthetic data.** `data/scripts/generate_demo_business_data.py` generates 36 months
(2023-11-01 to 2026-10-31) with a fixed seed, so two runs produce identical files (asserted by
a test that compares file hashes). It follows PLAN.md section 5.4: signups from a Poisson process
growing from 2 to 7 per month, plan mix 50/35/15, list prices 499, 1,999, and lognormal 8,000 to
25,000, 40 percent annual billing with a 10 percent discount, monthly churn 3.0, 1.5, and 0.6
percent by plan, expansion and contraction, weekday and quarter-end usage patterns, a usage lift
at the batch launch, a one-time price increase for new customers at month 20, a falling false
positive rate, and a latency drop after an optimization event. Tenant names are neutral word pairs
with generic suffixes. The result is 159 tenants, 121 active at the end, and an ending ARR of
about 6.0 million USD (target envelope 5 to 9 million). `make business` generates and loads it;
the loader is idempotent and upserts tenants so rows that reference a demo tenant survive a
reload. CSV files are rebuilt, not committed.

**Views are the single source of truth.** MRR is computed once per tenant per month in
`tenant_mrr_monthly`. Movements, dimension breakdowns, and totals are all derived from that
view, so movements reconcile to the MRR change by construction, and the tests assert it for every
month, together with the agreement of the dimension views and the invoices (billed subscription
lines equal the month's MRR). Metrics that need more than SQL (trailing windows, CAC, payback,
LTV) are computed in `revenue.py` from the same series, and `docs/metrics.md` defines them.

**Refresh.** `refresh_business_views()` is a `SECURITY DEFINER` function that refreshes all
views concurrently, so readers are never blocked and the API role does not own the views. It
runs nightly at 02:00 UTC from the worker, on demand from `POST /platform/revenue/refresh`, and
with `make business-refresh`. Each response reports `refreshed_at`.

**Forecast.** ETS with additive error and a damped additive trend, no seasonality (three annual
cycles are too few to estimate it), 80 percent prediction interval, 6 months. The response is
labeled an estimate and contains a holdout backtest (MAPE and interval coverage) so that its
accuracy is visible rather than asserted. The statsmodels import is done at start-up because it
costs 0.6 seconds, which would otherwise land on the first request.

**Gross margin.** The 80 percent gross margin used for payback and LTV is a stated assumption,
returned in the `assumptions` block so that the interface can show it.

**Endpoints.** Revenue routes run in platform scope regardless of `X-Tenant-Id` (the views are
cross-tenant), require the `revenue` permission, and accept range, comparison, and filters.
Revenue granularity is month only, because MRR is a month-end snapshot; asking for day or week
returns a problem response instead of a misleading series. Operational routes accept day, week,
and month, run in the caller's tenant scope (or platform scope), and require `ops_analytics`.
Responses are cached for 60 seconds, keyed by tenant scope so that one tenant's result is never
served to another. Any tabular result can be exported with `format=csv`; exports are audited.

**Honest gaps.** The comparison of the fuzzy baseline, BELA, and the hybrid reads
`docs/eval/results.json`, which the Phase 3 evaluation has not produced; the endpoint reports
`available: false` and nothing is estimated. The adverse category and language series depend on
ingested articles, which are few in the development database. The synthetic usage and alert
history is not connected to the live alert tables, so a tenant that has both sees them added in
the same series.

## Consequences

- A change to a metric definition is made in one view or one function and in `docs/metrics.md`.
- Dashboards read precomputed views, so revenue pages stay fast as the history grows (all
  revenue endpoints answer in under 25 ms on the seeded data).
- Monthly CAC is volatile with a handful of signups, so payback and LTV to CAC use the
  trailing three month figure, and both are shown.
