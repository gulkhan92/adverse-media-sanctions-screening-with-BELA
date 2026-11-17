# ADR-005: Backend API, authentication, tenancy, and audit

Status: accepted (phase 4)

## Context

The platform serves compliance teams. Several organizations (tenants) share one deployment and
must never see each other's subjects, alerts, or cases. Matches are leads for human review, so
every reviewer action must be attributable and the record of those actions must be tamper
evident. PLAN.md sections 6 (phase 4) and 10 define the requirements.

## Decisions

**Application structure.** FastAPI with synchronous handlers and a psycopg connection pool.
Routers are thin: request validation (Pydantic, `extra="forbid"`), a permission check, SQL, and
an audit entry. Every handler receives a `Ctx` holding the authenticated principal and one
transaction that is already bound to a tenant scope. Errors use RFC 7807 problem documents with
the request ID. Lists use opaque keyset cursors.

**Tenant isolation is enforced by the database.** The API connects as the `atlas_app` role,
which owns no objects, so Row Level Security applies to it. Each request runs
`set_config('app.tenant_id', ..., true)` (transaction local) and policies compare `tenant_id`
to it. Child tables without a tenant column (screening matches, alert events, case notes,
invoice lines, MFA and refresh token rows) are filtered through their parent. Watchlist,
entity, and article tables are global: the role has `SELECT` only. A request with no scope sees
zero rows from tenant tables. Platform operators (`platform_admin`, `finance_analyst`) run with
`app.platform = 'on'`; a platform administrator may instead pick one tenant with `X-Tenant-Id`.
The platform setting is also used for pre-authentication lookups (login, reset, API key
lookup). It is set only by server code through bound parameters.

**Authentication.**
- Argon2id (time 3, memory 64 MiB, parallelism 4), policy of at least 12 characters and a
  zxcvbn score of 3 or more, no composition rules. Unknown accounts are verified against a decoy
  hash so timing does not reveal account existence. Login errors are identical for unknown
  account, wrong password, inactive account, and locked account.
- Failed logins increase a counter; from the fifth failure the account is locked for
  `min(60, 2^(n-5))` minutes. Per-IP and per-account login rate limits apply in addition.
- TOTP (RFC 6238) with a QR enrollment image and 10 single-use recovery codes stored as keyed
  hashes. The TOTP secret is stored with `pgp_sym_encrypt`. `tenant_admin` and `platform_admin`
  cannot obtain a session until MFA is enrolled: login returns a short-lived `mfa_setup`
  token instead.
- Access token: EdDSA JWT, 15 minutes, in an `HttpOnly; Secure; SameSite=Strict` cookie. The
  token carries a session ID; each request checks that the session family is not revoked and
  that the user is still active, so logout and deactivation take effect immediately.
- Refresh token: opaque 256-bit random value stored as a keyed hash, path-scoped to
  `/api/v1/auth`, rotated on every use. A second use of an already used token revokes the whole
  session family. Idle (8 h) and absolute (7 d) lifetimes are configurable.
- CSRF: double-submit token (`atlas_csrf` cookie and `X-CSRF-Token` header) required for every
  state-changing request authenticated by cookie. The token survives refresh rotation.
  API-key requests carry no cookies and are not subject to CSRF.
- API keys: `atl_<8 hex>.<secret>`, secret stored as a keyed hash and shown once, bound to one
  tenant, limited to the scopes `view`, `screen`, `resolve`, `cases`, `ops_analytics` and never
  more than the creator holds, with expiry and last-use tracking.
- Password reset and invitations use single-use hashed tokens with a time limit. The forgot
  endpoint returns the same response for every address.

**Authorization.** A role maps to a permission set (`rbac.py`, PLAN.md section 10). Every route
declares its permission; denials are written to the audit log. The matrix is tested for every
role against every endpoint group, and cross-tenant access is tested through the API and
directly against the database.

**Audit log.** `audit_log` accepts inserts only. A `BEFORE INSERT` trigger running as the table
owner serialises writers with an advisory lock, links each row to the previous row and stores
`sha256(prev | id | tenant | actor | action | target | detail | request_id | timestamp)`.
`UPDATE`, `DELETE`, and `TRUNCATE` are revoked from the application role and also blocked by
triggers for the owner. `audit_verify()` recomputes the chain and returns the first invalid row;
it is exposed at `/admin/audit/verify` and as `make audit-verify`. Values whose names suggest a
secret are redacted before storage.

**Abuse controls.** Fixed-window rate limits per IP, per user, per API key, per login account,
for password reset, and for the public contact form. The backend is Valkey when
`ATLAS_VALKEY_URL` is set and process memory otherwise (single process only). Request bodies are
limited to 1 MB (5 MB for batch) by an ASGI middleware that counts bytes as well as checking
`Content-Length`. Batch submissions require an `Idempotency-Key`; a replay returns the stored
response and a changed body under the same key is rejected.

**Live updates.** `/api/v1/stream/alerts` is a Server-Sent Events stream. Alert changes publish
to a per-tenant channel (Valkey pub/sub, or an in-process bus). The stream authenticates once,
holds no database connection, and sends a heartbeat every 15 seconds.

**Data protection.** Subject dates of birth are stored with `pgp_sym_encrypt` and returned only
on the single-subject read. Security headers (CSP, `nosniff`, frame denial, referrer and
permissions policy, HSTS) are set on every response; CORS is not enabled.

## Consequences

- Isolation does not depend on every query remembering a `WHERE tenant_id` clause. A missing
  clause returns no rows rather than another tenant's rows.
- The pool reuses connections, so scope values are always transaction local.
- Batch screening runs inside the request in this phase. Phase 5 moves it to a Celery queue
  behind the same endpoints.
- Encryption keys, the signing key, and the token pepper come from the environment. The process
  refuses to start in `production` mode with development defaults.
