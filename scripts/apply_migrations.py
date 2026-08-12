"""Apply Kawa Phase 0 SQL migrations in order (sql/NNNN_*.sql), each EXACTLY ONCE.

Usage:  KAWA_DSN=dbname=kawa python scripts/apply_migrations.py

A `schema_migrations` ledger records which files have been applied, so re-running is a
no-op for already-applied files. This is load-bearing correctness, not convenience: some
migrations legitimately supersede an earlier one's state (e.g. a later migration widens a
CHECK constraint), and blindly re-running the earlier file would revert that and then fail
against rows the newer state allows. Each file runs in its own transaction; the ledger row
commits with it, so a crash never marks an unapplied file as applied. Stop-on-first-error.
"""
from __future__ import annotations

import glob
import os
import sys

import psycopg

_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT clock_timestamp()
)
"""


def main() -> int:
    dsn = os.environ.get("KAWA_DSN", "dbname=kawa")
    root = os.path.join(os.path.dirname(__file__), os.pardir, "sql")
    files = sorted(glob.glob(os.path.join(root, "*.sql")))
    if not files:
        print("no sql/*.sql migrations found", file=sys.stderr)
        return 1
    with psycopg.connect(dsn) as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_LEDGER)
            cur.execute("SELECT filename FROM schema_migrations")
            applied = {r[0] for r in cur.fetchall()}
        newly = 0
        for path in files:
            name = os.path.basename(path)
            if name in applied:
                continue
            sql = open(path, encoding="utf-8").read()
            try:
                with conn.transaction(), conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (name,))
                print(f"applied {name}")
                newly += 1
            except Exception as exc:  # stop-on-error
                print(f"FAILED {name}: {exc}", file=sys.stderr)
                return 1
    print(f"ok — {newly} newly applied, {len(files)} total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
