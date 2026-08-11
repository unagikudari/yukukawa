"""PostgreSQL connection for Kawa Phase 0.

Local-first: the default DSN is the on-node `kawa` database over the unix socket (peer auth),
so no secret lives in the repo or environment. A remote/service profile can override via KAWA_DSN.
"""
from __future__ import annotations

import os

import psycopg


def default_dsn() -> str:
    return os.environ.get("KAWA_DSN", "dbname=kawa")


def connect(dsn: str | None = None) -> psycopg.Connection:
    return psycopg.connect(dsn or default_dsn(), autocommit=False)
