"""Orient a fresh session from Kawa's live state (context.bootstrap).

Usage:  KAWA_DSN=dbname=kawa python scripts/brief.py
"""
from kawa.brief import bootstrap, render
from kawa.storage.db import connect

if __name__ == "__main__":
    print(render(bootstrap(connect())))
