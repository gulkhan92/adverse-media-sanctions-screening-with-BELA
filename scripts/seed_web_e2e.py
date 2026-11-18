"""Create or reset the two development users used by apps/web/scripts/e2e.mjs.

An analyst without MFA and a platform administrator whose MFA enrollment is cleared, so that the
enrollment flow can run again. Usage: E2E_PASSWORD=... python scripts/seed_web_e2e.py
"""

from __future__ import annotations

import os
import sys

import psycopg
from atlas.api.security import PasswordPolicyError, check_password_policy, make_hasher
from atlas.api.settings import Settings

ADMIN_URL = os.environ.get("ATLAS_ADMIN_DATABASE_URL", "postgresql://atlas@127.0.0.1:54329/atlas")
ANALYST = "e2e.analyst@example.org"
PLATFORM = "e2e.platform@example.org"


def main() -> int:
    password = os.environ.get("E2E_PASSWORD", "")
    try:
        check_password_policy(password, ["e2e"])
    except PasswordPolicyError as error:
        print(f"E2E_PASSWORD rejected: {error}", file=sys.stderr)
        return 1
    hashed = make_hasher(Settings()).hash(password)
    with psycopg.connect(ADMIN_URL) as conn:
        row = conn.execute("SELECT id FROM tenants ORDER BY created_at LIMIT 1").fetchone()
        if row is None:
            print("no tenant exists; run `make business` first", file=sys.stderr)
            return 1
        conn.execute(
            "DELETE FROM users WHERE email IN (%s, %s)", (ANALYST, PLATFORM)
        )  # cascades to MFA and session rows
        conn.execute(
            "INSERT INTO users (tenant_id, email, password_hash, role) "
            "VALUES (%s, %s, %s, 'analyst')",
            (row[0], ANALYST, hashed),
        )
        conn.execute(
            "INSERT INTO users (tenant_id, email, password_hash, role) "
            "VALUES (NULL, %s, %s, 'platform_admin')",
            (PLATFORM, hashed),
        )
    print("seeded", ANALYST, PLATFORM)
    return 0


if __name__ == "__main__":
    sys.exit(main())
