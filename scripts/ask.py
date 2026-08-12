#!/usr/bin/env python3
"""Narrow dogfood harness for unified retrieval (#100 rev 2, review point 5).

Calls ONLY the internal `kawa.retrieval.retrieve` entry point; exposes NO stable external
schema; exists to exercise acceptance cases and inspect provenance/frontier behavior. The
step-6 MCP surface will wrap `retrieve` directly — no compatibility promise to this CLI.

Usage:  KAWA_DSN=dbname=kawa python scripts/ask.py [--about REF] [--text "terms"]
                                                   [--depth N] [--limit N]
"""
from __future__ import annotations

import argparse

from kawa.retrieval import Intent, retrieve
from kawa.storage.db import connect


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--about")
    ap.add_argument("--text")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    conn = connect()
    bundle = retrieve(conn, Intent(about=args.about, text_terms=args.text,
                                   relation_depth=args.depth, limit=args.limit))
    p = bundle.plan
    print(f"bound: anchor={p.bound.anchor_kind}:{p.bound.anchor_ref} "
          f"textual={p.bound.unbound_text is not None}")
    print("plan:  " + (", ".join(f"{q.class_id}[{q.backend} b={q.budget}"
                                 + (f" {q.fts_reason}" if q.fts_reason else "") + "]"
                                 for q in p.query_classes) or "(empty)"))
    for class_id, records in bundle.sections.items():
        print(f"\n== {class_id} ({len(records)}) ==")
        for r in records:
            standing = f"  [standing={r.standing}]" if r.standing else ""
            trunc = "  [path_truncated]" if r.path_truncated else ""
            print(f"  {r.kind:22s} {r.summary[:80]}{standing}{trunc}")
            print(f"    via {r.path[:100]}")
    if bundle.fts_queries:
        print("\nfts: " + "; ".join(bundle.fts_queries))
    if bundle.empty_classes:
        print(f"\nempty classes (ran, found nothing): {', '.join(bundle.empty_classes)}")
    if bundle.skipped_classes:
        print(f"skipped classes (deferred, did not fire): {', '.join(bundle.skipped_classes)}")
    if bundle.traversal_frontier:
        reasons = {}
        for f in bundle.traversal_frontier:
            reasons[f.reason] = reasons.get(f.reason, 0) + 1
        print(f"traversal frontier (not expanded): {reasons}")
    if bundle.unresolved_frontier:
        print(f"unresolved links (targets not held): {len(bundle.unresolved_frontier)}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
