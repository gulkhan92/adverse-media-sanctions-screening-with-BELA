# API reference (phase 4)

Interactive documentation is served at `/api/docs` (OpenAPI at `/api/openapi.json`). All routes
are under `/api/v1`. Screening results are leads for human review, not determinations, and the
disclaimer is part of every screening and alert response.

Run locally: `make db-up migrate valkey-up api-up` (Valkey is optional; without
`ATLAS_VALKEY_URL` the limiter and event bus run in process). Create the first administrator
with `python -m atlas.api.cli create-user EMAIL platform_admin`.

## Conventions

- Errors are `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`,
  and `request_id`. Validation errors list field names and messages only.
- Lists return `{items, next_cursor}`; pass `cursor` and `limit` (maximum 100).
- Send or receive `X-Request-ID` (8 to 64 characters of `A-Za-z0-9._-`).
- Cookie sessions require `X-CSRF-Token` equal to the `atlas_csrf` cookie on `POST`, `PUT`,
  `PATCH`, and `DELETE`. API keys are sent as `X-API-Key: atl_xxxxxxxx.secret`.
- Platform administrators select a tenant for tenant-scoped routes with `X-Tenant-Id`.
- Limits: 1 MB body (5 MB for batch), up to 1000 subjects per batch, 300 requests per minute
  per IP, 240 per user, 600 per API key, 20 login attempts per 5 minutes per IP.

## Permissions

| Permission | Roles |
|---|---|
| `view` | viewer, analyst, manager, tenant admin, platform admin |
| `screen`, `resolve`, `cases` | analyst, manager, tenant admin, platform admin |
| `ops_analytics` | manager, tenant admin, platform admin |
| `admin`, `audit` | tenant admin, platform admin |
| `revenue` | finance analyst, platform admin |
| `platform` | platform admin |

## Endpoints

| Area | Route | Permission |
|---|---|---|
| Auth | `POST /auth/login`, `/auth/mfa/setup`, `/auth/mfa/verify`, `/auth/refresh`, `/auth/logout`, `/auth/password/forgot`, `/auth/password/reset` | public (rate limited) |
| Auth | `GET /auth/me`, `GET /auth/sessions`, `DELETE /auth/sessions/{id}` | signed in |
| Screening | `POST /screenings`, `POST /screenings/batch` (needs `Idempotency-Key`), `POST /screenings/text` | `screen` |
| Screening | `GET /screenings/{id}`, `GET /screenings/batch/{id}` | `view` |
| Subjects | `GET /subjects`, `GET /subjects/{id}` | `view` |
| Subjects | `POST /subjects`, `PATCH /subjects/{id}`, `POST /subjects/{id}/monitor`, `DELETE /subjects/{id}` | `screen` |
| Alerts | `GET /alerts`, `GET /alerts/{id}` | `view` |
| Alerts | `PATCH /alerts/{id}`, `POST /alerts/{id}/comments` | `resolve` |
| Cases | `GET/POST /cases`, `GET/PATCH /cases/{id}` | `cases` |
| Knowledge | `GET /entities/search`, `/entities/{id}`, `/graph/neighbors`, `/graph/path`, `/watchlists/sources`, `/watchlists/entries`, `/articles`, `/articles/{id}` | `view` |
| Analytics | `GET /analytics/overview` | `view` |
| Analytics | `GET /analytics/operations`, `/usage`, `/kpis`, `/volume`, `/severity`, `/quality`, `/latency`, `/languages`, `/categories`, `/risk-by-country`, `/queue-ageing`, `/throughput`, `/routing`, `/hybrid-vs-baseline` | `ops_analytics` |
| Platform | `GET /platform/revenue/summary`, `/kpis`, `/mrr`, `/movements`, `/mix`, `/forecast`, `/cohorts`, `/dimensions`, `/concentration`, `/unit-economics`, `/billing-health`, `/customers`, `/customers/{id}`, `POST /platform/revenue/refresh` (synthetic, labeled demo data) | `revenue` |
| Platform | `GET /tenants`, `POST /tenants`, `GET /ingestion/status`, `GET /admin/audit/verify` | `platform` |
| Admin | `GET/POST /admin/users`, `PATCH /admin/users/{id}`, `GET/POST /admin/api-keys`, `DELETE /admin/api-keys/{id}`, `GET/PUT /admin/risk-config`, `GET /billing/plan`, `GET /billing/invoices` | `admin` |
| Admin | `GET /admin/audit` | `audit` |
| Other | `GET /tenants/current`, `GET /users/me`, `POST /contact/demo-request` (public, honeypot, rate limited), `GET /health/live`, `GET /health/ready`, `GET /stream/alerts` (SSE, `view`) | as listed |

## Alert review rules

Allowed status changes: `new` to any of `in_review`, `escalated`, `true_match`,
`false_positive`, `closed`; `in_review` back to `new` or on to any decision; `escalated` to a
decision or back to `in_review`; a decided or closed alert can only be reopened to
`in_review`. Decisions (`true_match`, `false_positive`, `closed`, `escalated`) require a written
reason of at least three characters. Every change adds an `alert_events` row and an audit entry.

## Risk score

`score = 100 * (0.40 * list_weight * match_confidence + 0.35 * severity * link_confidence
+ 0.15 * recency + 0.10 * reliability)`. Default list weights: sanctions 1.0, PEP 0.6, other
lists 0.5, adverse media only 0.4. Levels: critical at 80 or above, high 60 to 79, medium 40 to
59, low below 40. Every screening and alert stores `score_breakdown` with the weight, inputs, and
points of each component. `PUT /admin/risk-config` accepts changes only inside fixed bounds
(see ADR-006) and versions each change. An exact list match with no adverse media scores 65.

Match confidence is name similarity adjusted by country (+0.08 agree, -0.10 conflict) and birth
year (+0.12 agree, -0.25 conflict), or 1.0 for an exact identifier. The weights are the plan
defaults and are not fitted to labeled outcomes; reviewer decisions are stored in
`alert_feedback` so that they can be.

## Batch screening

1. `GET /screenings/batch/template` returns the CSV template (`name`, `kind`, `country`, `dob`,
   `identifier_type`, `identifier_value`, `aliases` separated by `;`).
2. `POST /screenings/batch/upload` (multipart `file`) validates up to 10,000 rows and returns
   `202` with the batch ID, the accepted count, and the rejected rows with reasons.
3. `GET /screenings/batch/{id}` reports `status` (`queued`, `running`, `completed`, `failed`),
   `progress`, `completed`, `failed`, per-row `errors`, and results.
4. `GET /screenings/batch/{id}/export` returns the results as CSV.

`POST /screenings/batch` (JSON, up to 1000 subjects, `Idempotency-Key` required) remains for small
synchronous batches.

## Monitoring and alerts

`POST /subjects/{id}/monitor` turns monitoring on and scans that subject at once. The scheduled
task (`make worker` and `make beat`) rescans monitored subjects when new mentions or list entries
exist. Alerts are de-duplicated by subject, article, and list entry. New alerts arrive on
`/stream/alerts` as `alert.created`; status changes arrive as `alert.updated`.

## Reports and graph

`GET /screenings/{id}/report.pdf` returns the PDF report and records it (`X-Report-ID`).
`GET /graph/neighbors` and `/graph/path` combine registry relationships with article
co-occurrence edges; each edge lists its provenance, and a response is flagged `truncated` at 500
nodes. Cases can group alerts and entities (`add_alert_ids`, `add_entity_ids`).

## Running workers

    make valkey-up                    # broker and event bus
    export ATLAS_CELERY_BROKER_URL=redis://127.0.0.1:6379/1 ATLAS_VALKEY_URL=redis://127.0.0.1:6379/0
    make worker                       # queues match, ingest, nlp, reports
    make beat                         # monitoring every 5 minutes, statistics every 15

Without a broker the API processes uploads itself after responding. The worker connects as the
`atlas_worker` role (`ATLAS_WORKER_DATABASE_URL`).

## Measured performance

- Single screening (exact, reversed, and misspelled names against 19,563 loaded OFAC entries,
  150 sequential requests): p50 5 ms, p95 10 ms (`docs/api_latency.md`).
- A 10,000 row CSV upload processed in the API process: 35.7 seconds in the test suite (three
  list entries, local database). A 303 row upload through a Celery worker and Valkey against the
  development database finished in 1.0 second.
- Both are single-machine laptop measurements. The load test with concurrent users is part of
  phase 9.

## Dashboard endpoints (phase 6)

Revenue endpoints (`/platform/revenue/*`) and the operational endpoints under `/analytics`
share these query parameters:

| Parameter | Values |
|---|---|
| `range` | `30d`, `90d`, `12m` (default), `all`, `custom` with `from` and `to` |
| `granularity` | `day`, `week`, `month`. Revenue accepts `month` only |
| `compare` | `true` adds the previous period of the same length |
| `plan`, `region`, `industry`, `channel` | Revenue filters (see `docs/metrics.md`) |
| `format` | `csv` exports the table behind the chart |

Revenue routes always run across all tenants and ignore `X-Tenant-Id`. Responses carry
`as_of`, `refreshed_at`, and `X-Cache`. Definitions, formulas, and assumptions are in
`docs/metrics.md`. The business data is generated with `make business` (seed 42, 36 months,
ending ARR about 6.0 million USD) and the views are refreshed nightly by the worker.

Measured on the seeded database (cold cache, three parameter sets per endpoint, one machine):
every revenue endpoint answers in under 25 ms, and every operational endpoint in under 500 ms
(asserted by the tests).
