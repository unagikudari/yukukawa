"""PostgreSQL connection for Kawa Phase 0.

FAIL-CLOSED (#129 step 12A, round-2 F1): there is NO default database. The old
implicit default (`KAWA_DSN` unset => the dogfood `kawa` DB) was the escape
hatch every automation accident travels through — a test fixture reached the
production log through exactly that door on 2026-08-12 (plan-2026-08-12-log-loss).
The dogfood database is only ever reached by NAMING it: every entrypoint goes
through `connect()`, so the refusal covers migrations, scripts, and services
structurally, not by enumeration.
"""
from __future__ import annotations

import os

import psycopg


def default_dsn() -> str:
    dsn = os.environ.get("KAWA_DSN")
    if not dsn:
        raise RuntimeError(
            "KAWA_DSN is not set and Kawa refuses to guess a database "
            "(#129 12A fail-closed). Name the target explicitly, e.g. "
            "KAWA_DSN='dbname=kawa' for the dogfood log.")
    return dsn


def connect(dsn: str | None = None) -> psycopg.Connection:
    return psycopg.connect(dsn or default_dsn(), autocommit=False)
