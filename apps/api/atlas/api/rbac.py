"""Role to permission matrix (PLAN.md section 10). Enforced on the server for every route."""

from __future__ import annotations

VIEW = "view"
SCREEN = "screen"
RESOLVE = "resolve"
CASES = "cases"
OPS = "ops_analytics"
ADMIN = "admin"
AUDIT = "audit"
REVENUE = "revenue"
PLATFORM = "platform"

ALL_PERMISSIONS = frozenset({VIEW, SCREEN, RESOLVE, CASES, OPS, ADMIN, AUDIT, REVENUE, PLATFORM})
# Scopes that an API key may carry. Revenue, platform, and audit access are never delegable.
KEY_SCOPES = frozenset({VIEW, SCREEN, RESOLVE, CASES, OPS})

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({VIEW}),
    "analyst": frozenset({VIEW, SCREEN, RESOLVE, CASES}),
    "manager": frozenset({VIEW, SCREEN, RESOLVE, CASES, OPS}),
    "tenant_admin": frozenset({VIEW, SCREEN, RESOLVE, CASES, OPS, ADMIN, AUDIT}),
    "finance_analyst": frozenset({REVENUE}),
    "platform_admin": ALL_PERMISSIONS,
}
MFA_REQUIRED_ROLES = frozenset({"tenant_admin", "platform_admin"})
PLATFORM_SCOPE_ROLES = frozenset({"platform_admin", "finance_analyst"})
TENANT_ROLES = ("viewer", "analyst", "manager", "tenant_admin")
