"""Orient a fresh session from Kawa's live state (context.bootstrap).

Usage:  KAWA_DSN=dbname=kawa python scripts/brief.py

Also prints node-local resident health when something is wrong. That part is
NOT Kawa state (see kawa/nodehealth.py) — it is composed here, after the
brief, so a failed collector on THIS machine reaches the reader that every
session-start hook already has, instead of sitting in journald unread.
"""
from kawa import nodehealth
from kawa.brief import bootstrap, render
from kawa.storage.db import connect

if __name__ == "__main__":
    print(render(bootstrap(connect())))
    try:
        health = nodehealth.render(nodehealth.scan())
    except Exception as exc:            # orientation must survive a broken status dir
        health = f"(node health unavailable: {type(exc).__name__}: {exc})"
    if health:
        print()
        print(health)
