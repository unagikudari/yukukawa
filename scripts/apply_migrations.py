"""Apply Kawa Phase 0 SQL migrations in order (sql/NNNN_*.sql), stop on first error.

Usage:  KAWA_DSN=dbname=kawa python scripts/apply_migrations.py
The default DSN is `dbname=kawa` (local unix socket). Each file runs in its own
transaction; a failure aborts that file and stops the run (ON_ERROR_STOP semantics).
"""
from __future__ import annotations

import glob
import os
import sys

import psycopg


def main() -> int:
    dsn = os.environ.get("KAWA_DSN", "dbname=kawa")
    root = os.path.join(os.path.dirname(__file__), os.pardir, "sql")
    files = sorted(glob.glob(os.path.join(root, "*.sql")))
    if not files:
        print("no sql/*.sql migrations found", file=sys.stderr)
        return 1
    with psycopg.connect(dsn) as conn:
        for path in files:
            name = os.path.basename(path)
            sql = open(path, encoding="utf-8").read()
            try:
                with conn.transaction(), conn.cursor() as cur:
                    cur.execute(sql)
                print(f"applied {name}")
            except Exception as exc:  # stop-on-error
                print(f"FAILED {name}: {exc}", file=sys.stderr)
                return 1
    print(f"ok — {len(files)} migration(s) applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
