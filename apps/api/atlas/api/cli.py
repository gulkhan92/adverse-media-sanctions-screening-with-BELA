"""Operator commands that run outside the request path.

  python -m atlas.api.cli create-tenant NAME
  python -m atlas.api.cli create-user EMAIL ROLE [--tenant TENANT_ID]   (prompts for password)
  python -m atlas.api.cli verify-audit

Commands connect with ATLAS_ADMIN_DATABASE_URL (the table owner), because the API role cannot
create users before any exist. The first platform administrator is created this way.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

import psycopg

from atlas.api.rbac import ROLE_PERMISSIONS, TENANT_ROLES
from atlas.api.security import PasswordPolicyError, check_password_policy, make_hasher
from atlas.api.settings import Settings

ADMIN_URL = os.environ.get("ATLAS_ADMIN_DATABASE_URL", "postgresql://atlas@127.0.0.1:54329/atlas")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    tenant = sub.add_parser("create-tenant")
    tenant.add_argument("name")
    user = sub.add_parser("create-user")
    user.add_argument("email")
    user.add_argument("role", choices=sorted(ROLE_PERMISSIONS))
    user.add_argument("--tenant")
    sub.add_parser("verify-audit")
    args = parser.parse_args(argv)
    with psycopg.connect(ADMIN_URL) as conn:
        if args.command == "create-tenant":
            row = conn.execute(
                "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (args.name,)
            ).fetchone()
            print(row[0] if row else "")
        elif args.command == "create-user":
            if args.role in TENANT_ROLES and not args.tenant:
                parser.error("--tenant is required for tenant roles")
            password = getpass.getpass("Password: ")
            try:
                check_password_policy(password, [args.email.split("@")[0], args.email])
            except PasswordPolicyError as error:
                print(f"Rejected: {error}", file=sys.stderr)
                return 1
            hashed = make_hasher(Settings()).hash(password)
            conn.execute(
                "INSERT INTO users (tenant_id, email, password_hash, role) VALUES (%s, %s, %s, %s)",
                (
                    args.tenant if args.role in TENANT_ROLES else None,
                    args.email.lower(),
                    hashed,
                    args.role,
                ),
            )
            print("created; multi-factor enrollment is required at first sign-in for admin roles")
        else:
            row = conn.execute("SELECT audit_verify()").fetchone()
            bad = row[0] if row else None
            print("audit chain intact" if bad is None else f"audit chain broken at id {bad}")
            return 0 if bad is None else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
