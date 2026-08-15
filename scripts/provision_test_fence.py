"""Step 12A test fence provisioning (#129 rev 3 F1) — OPERATOR action, superuser.

Usage:  SUPERUSER_DSN='host=127.0.0.1 user=membroker password=... dbname=postgres' \
            python scripts/provision_test_fence.py [--test-dbs kawa_test_a,kawa_test_b]

Creates the fenced `kawa_test` role pytest actually runs under, and closes the
dogfood door at the DATABASE level:

  - CREATE ROLE kawa_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  - REVOKE CONNECT ON DATABASE kawa FROM PUBLIC   <- the load-bearing revoke:
    a role-targeted revoke is a no-op while PUBLIC still holds CONNECT
  - GRANT CONNECT back to the owner role only (superusers bypass anyway)
  - full privileges for kawa_test on the test databases (incl. default
    privileges for future migration-created tables)

The fence is an ACCIDENT barrier inside one trust domain (the policy doc says
so explicitly) — the negative control in tests/test_durability_fence.py proves
it holds for the credential pytest really uses. Idempotent; rerun-safe."""
from __future__ import annotations

import argparse
import os
import sys

import psycopg

FENCE_ROLE = "kawa_test"
FENCE_PASSWORD = "kawa_test"        # accident fence, not a secret (policy doc)
DOGFOOD_DB = "kawa"
OWNER_ROLE = "yurei"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-dbs", default="kawa_test_a,kawa_test_b")
    args = ap.parse_args()
    dsn = os.environ.get("SUPERUSER_DSN")
    if not dsn:
        print("SUPERUSER_DSN required (fence provisioning is an operator action)",
              file=sys.stderr)
        return 1
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (FENCE_ROLE,))
        if not cur.fetchone():
            # DDL cannot be parameterized; both values are module constants
            cur.execute(f"CREATE ROLE {FENCE_ROLE} LOGIN PASSWORD '{FENCE_PASSWORD}' "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE")
            print(f"created role {FENCE_ROLE}")
        cur.execute(f"REVOKE CONNECT ON DATABASE {DOGFOOD_DB} FROM PUBLIC")
        cur.execute(f"REVOKE CONNECT ON DATABASE {DOGFOOD_DB} FROM {FENCE_ROLE}")
        cur.execute(f"GRANT CONNECT ON DATABASE {DOGFOOD_DB} TO {OWNER_ROLE}")
        print(f"dogfood CONNECT: PUBLIC revoked, {OWNER_ROLE} granted")
        for db in args.test_dbs.split(","):
            cur.execute(f"GRANT CONNECT, TEMP ON DATABASE {db} TO {FENCE_ROLE}")
    for db in args.test_dbs.split(","):
        with psycopg.connect(f"{dsn.rsplit('dbname=', 1)[0]}dbname={db}",
                             autocommit=True) as c2, c2.cursor() as cur:
            cur.execute(f"GRANT USAGE ON SCHEMA public TO {FENCE_ROLE}")
            cur.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {FENCE_ROLE}")
            cur.execute(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {FENCE_ROLE}")
            for creator in (OWNER_ROLE, "membroker"):   # PR #130 review: superuser
                # migrations must not leak tables the fence role cannot reach
                cur.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {creator} IN SCHEMA public "
                            f"GRANT ALL ON TABLES TO {FENCE_ROLE}")
                cur.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {creator} IN SCHEMA public "
                            f"GRANT ALL ON SEQUENCES TO {FENCE_ROLE}")
            print(f"granted {FENCE_ROLE} full test privileges on {db}")
    print("fence provisioned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
