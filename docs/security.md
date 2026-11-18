# Security verification (phase 4)

This document records what was verified in this repository and what was not.

## Automated tests

`make api-test` runs the API and workflow tests (163 tests in the whole suite) against a throwaway PostgreSQL
database that is migrated from scratch, with the API connected as the restricted `atlas_app`
role. Line coverage of `atlas.api` and `atlas.jobs` is 90 percent (the CI threshold is 80).

| Area | What is asserted |
|---|---|
| Authentication | cookie flags; identical failures for unknown and wrong credentials; lockout; login rate limit; MFA enrollment gate for admin roles; TOTP and single-use recovery codes stored hashed; MFA token rejected as a session; CSRF required and checked; refresh rotation; reuse detection revokes the family and the legitimate holder; logout and deactivation revoke access immediately; session listing and revocation; reset flow with no enumeration, hashed single-use token, weak password rejection, session revocation; API key lifecycle, scope limits, hashed storage |
| Authorization | six roles against 25 endpoints (allowed and denied), unauthenticated access to all of them, denials audited |
| Tenant isolation | through the API: read, change, comment, screen, monitor, delete, case attach, assignee and owner from another tenant are all rejected; tenant header ignored for tenant users; API keys bound to their tenant. Directly in the database: no scope returns zero rows from tenant tables, a tenant scope returns only its rows, cross-tenant `UPDATE` affects zero rows, cross-tenant `INSERT` is refused, global tables are read-only for the role |
| Audit | chain intact after normal use; tampering is detected at the first altered row; `UPDATE`, `DELETE`, `TRUNCATE` refused for the application role and for the owner while triggers are active; request ID recorded; no secrets in stored detail |
| Abuse controls | per-IP, per-user, per-API-key, login, reset, and contact limits with `Retry-After`; 413 for oversize bodies; batch limits; idempotent replay and conflict; honeypot |
| Transport and headers | CSP, `nosniff`, frame denial, referrer and permissions policy, HSTS, `no-store`, no CORS headers, request ID echo and sanitising |
| Real time | SSE over a real HTTP server delivers an alert update only to the owning tenant; unauthenticated stream refused; Valkey rate limit and pub/sub backends |
| Workflows | score formula and breakdown sums; configuration bounds rejected at the API; CSV validation, partial failure isolation, encrypted input wiped after processing, formula neutralisation in exports, batch privacy across tenants and roles; monitoring de-duplication, tenant scoping, and event delivery only to the owning tenant; HTML escaping and link filtering in the PDF report; end-to-end flow (subject, monitoring, fixture article, alert over SSE, review, report) |
| Analytics | revenue routes only for finance and platform roles (manager, tenant admin, viewer refused), tenant header ignored on revenue routes, operational data scoped to the caller's tenant, cache never shared between tenants, CSV exports audited and refreshes audited |
| Data protection | date of birth not present in the stored bytes, absent from list responses |

## Static analysis

- `ruff check` and `mypy --strict` (core settings) pass on `apps/api/atlas/api` and `apps/api/atlas/jobs`.
- `bandit -ll` reports no medium or high findings. Remaining low findings are `assert`
  statements used for type narrowing after `RETURNING` and one false positive on a schema
  example string.
- `pip-audit` reports no known vulnerabilities in the installed dependencies.
- CI also runs a forbidden-character check and gitleaks.

## Not done

- **OWASP ZAP baseline scan was not run.** The phase 4 exit criterion names it, but no container
  runtime is available in the development environment. The scan is a required step before the
  phase 9 release gate: run `zap-baseline.py -t http://127.0.0.1:8000/api/openapi.json -f openapi`
  against a running instance.
- TLS termination, HSTS preload, and encrypted disks are deployment concerns handled in phase 10
  (Caddy). The API sets `Secure` cookies by default and HSTS when `ATLAS_COOKIE_SECURE` is on.
- The password reset and invitation messages are held in an in-memory outbox unless
  `ATLAS_SMTP_HOST` is set (Mailpit in development). Delivery is not tested.
- Rate limits keyed by IP trust `X-Forwarded-For` only when `ATLAS_TRUST_PROXY=1`; configure it
  only behind a proxy that overwrites that header.
- The platform scope setting (`app.platform`) is trusted server input. A SQL injection flaw that
  could run `set_config` would bypass isolation; all queries use bound parameters and the one
  dynamic fragment was removed after static analysis flagged it.
- Retention: the per-tenant retention value is stored in the risk configuration, but the
  scheduled purge and the hard delete of soft-deleted subjects are not implemented yet (they need
  the job queue added in phase 5). The `identifiers_enc` column is not populated by any endpoint.
- Column encryption uses a single symmetric key from the environment. Key rotation is manual.
- Score weights are the plan defaults. Their precision against reviewer outcomes is not measured; `alert_feedback` collects the labels for that.
- The worker role writes global ingestion tables. It must run on a private network, with its database URL kept out of the API container.
- Uploaded CSV rows are encrypted at rest until processed; the file itself is read into memory and never stored.
- The PDF renderer blocks all external fetches, but report text comes from stored article excerpts, which are untrusted: they are escaped and only http and https links are emitted.

## Performance note

Single screening latency against the 19,563 loaded watchlist entries, measured with
`scripts/api_latency.py` over 150 sequential requests: p50 5 ms, p95 10 ms
(`docs/api_latency.md`). This is a laptop measurement with the client, API, and database on one
machine, and it covers watchlist matching only (no live entity linking).
