# Metric definitions

This file is the single source of truth for every business and operational metric. The SQL is
in `apps/api/migrations/versions/0006_business_views.py` (views) and
`apps/api/atlas/api/revenue.py` and `ops_analytics.py` (queries). Revenue data is synthetic and
every revenue response carries `demo_data: true` and the label "Demo data".

## Revenue base

| Object | Definition |
|---|---|
| `tenant_mrr_monthly` | One row per tenant per month end from the signup month on: the monthly-equivalent recurring fee of the subscriptions active at that month end (annual price divided by 12). A churned tenant keeps a zero row and its last plan. |
| `mrr_monthly` | Sum of `tenant_mrr_monthly` by month end, with the active customer count. |
| `tenant_mrr_movements` | Each tenant row compared with the previous month: `new` (previous 0, now above 0), `expansion` (increase), `contraction` (decrease, still above 0), `churn` (now 0), otherwise `none`. |
| `mrr_movements` | Movements summed by month, with begin and end MRR and customer counts. |
| `revenue_by_dimension` | MRR and customers by month for plan, region, industry, and acquisition channel. |
| `usage_revenue_monthly` | Billed subscription, overage, credit, and total by invoice period. Overage is usage revenue and is not part of MRR. |

Refresh: nightly at 02:00 UTC by the `atlas.refresh_business_views` job, and on demand with
`POST /api/v1/platform/revenue/refresh` or `make business-refresh`. Views are refreshed
concurrently, so reads are never blocked. `refreshed_at` is returned with every response.

## Metrics

All revenue metrics are monthly snapshots; revenue endpoints reject `granularity=day|week`.

| Metric | Definition |
|---|---|
| MRR | Sum of monthly-equivalent recurring fees of subscriptions active at month end |
| ARR | MRR x 12 |
| Net new MRR | new + expansion - contraction - churn |
| Reconciliation | For every month, new + expansion - contraction - churn = end MRR - begin MRR, and begin MRR equals the previous month's end MRR (asserted in tests) |
| ARPA | MRR / active customers |
| Logo churn rate | Customers lost in the month / customers at the start of the month |
| Quick ratio | (new + expansion) / (contraction + churn); empty when the denominator is 0 |
| Net revenue retention (NRR) | Over the trailing 12 months: MRR now of the customers that had MRR 12 months ago / their MRR 12 months ago (includes expansion, contraction, and churn, excludes new customers) |
| Gross revenue retention (GRR) | Same base, but each customer counts at most their MRR 12 months ago: sum of min(now, then) / sum of then. Always at most 100 percent and at most NRR |
| Cohort retention | Share of a signup month's first-month MRR still billed N months later (`cohort_retention`) |
| CAC (monthly) | Marketing spend in the month / new customers in the month. Blended, or by channel with `channel=` |
| CAC (trailing 3 months) | Spend of the last three months / new customers of the last three months. Used for payback and LTV to CAC because monthly CAC is noisy with 2 to 7 signups |
| CAC payback (months) | Trailing 3 month CAC / (ARPA x gross margin) |
| Gross margin | Fixed assumption of 80 percent. It is not measured, and the interface must show it in a tooltip |
| LTV | ARPA x gross margin / monthly churn, with monthly churn the trailing 6 month average logo churn |
| LTV to CAC | LTV / trailing 3 month CAC |
| Customer concentration | Share of MRR of the top 10 customers at the latest month, with the cumulative (Pareto) share |

Filters (`plan`, `region`, `industry`, `channel`) apply to the tenant attributes at each month
(the plan is the plan at that month end; a churned tenant keeps its last plan). Marketing spend has
no tenant dimension, so CAC, payback, and LTV to CAC are empty with `plan`, `region`, or `industry`
filters. Filtered MRR values partition the total: the sum over all values of one dimension equals
the unfiltered MRR (asserted in tests).

Windows: `range=30d|90d|12m|all|custom`. For revenue a window is a whole number of months
(30d is the latest month, 90d the latest three, 12m the latest twelve). With `compare=true` the
previous period is the same number of months immediately before. The change is the value at the
end of the window minus the value at the end of the previous window.

## Forecast

An ETS model (additive error, additive damped trend, no seasonality; `statsmodels`
`ETSModel`) is fitted to the 36 monthly MRR values and projected 6 months ahead with an 80 percent
prediction interval. The response is labeled "Estimate" and includes a backtest: the model is
refitted on the first 30 months and compared with the last 6 (MAPE and how many months fell
inside the interval). Seasonality is not modeled because 36 months hold only three annual cycles.
The history is synthetic, so the forecast describes the generator, not a real business.

## Operational analytics

Sources: `usage_daily`, `alert_stats_daily`, and live alert tables. Tenant users see their own
rows (Row Level Security); platform operators see all tenants. The window ends at the latest
metered day, or at the latest live alert if that is newer. `granularity` is `day`, `week`, or
`month`.

| Metric | Definition |
|---|---|
| Screenings | Sum of `usage_daily.screenings` |
| Alerts created | Sum of the severity counts in `usage_daily.alerts_by_severity`; `live_alerts_created` counts rows of the alerts table |
| Precision | confirmed true matches / (confirmed true matches + false positives). Platform scope uses `alert_stats_daily`; tenant scope uses the tenant's reviewed alerts (`true_match` and `false_positive` by resolution date) |
| False positive rate | 1 - precision |
| Median time to review | Platform: average of the daily medians weighted by alerts. Tenant: median of resolution time minus creation time of the tenant's resolved alerts |
| p50 and p95 latency | Average of the daily values of `alert_stats_daily` (a system metric, not tenant data) |
| Language mix | Mentions by article language within the window |
| Adverse category mix | Findings by category and severity |
| Risk by country | Alerts per subject country, with severity weights low 1, medium 2, high 3, critical 4 |
| Queue ageing | Open alerts (new, in review, escalated) by age: under 1 day, 1 to 3, 3 to 7, 7 to 14, 14 to 30, over 30 days. The SLA threshold is `queue_sla_hours` (default 72); `sla_breaches` counts open alerts older than it |
| Analyst throughput | Alerts moved to a decision (true match, false positive, closed) per analyst per week |
| Routing | Mentions by route (BELA accept, BLINK, LLM, NIL) and outcome, as Sankey nodes and links |
| Hybrid versus baseline | Read from `docs/eval/results.json`. Reports `available: false` until the Phase 3 evaluation has produced it; nothing is estimated |

## Caching and export

Responses are cached in process for 60 seconds, keyed by endpoint, parameters, and tenant scope
(`X-Cache: hit|miss`). A refresh of the business views clears the cache. Add `format=csv` to export
the table behind a chart. Exports are audited, and text cells that start with a spreadsheet
formula character are prefixed with an apostrophe.
