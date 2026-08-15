"""Step 11B indexer: an asynchronous, restartable pass over materialized events
missing embeddings for the current model_identity (#122 (h)).

Usage:
  KAWA_DSN=dbname=kawa python scripts/embed_index.py [--batch 64] [--watch SECONDS]

Event recording NEVER waits on this process (§10.6 MUST NOT block): it reads
events, writes only the two §12.2 derived tables, and commits per batch — kill
it anywhere and rerun; it resumes where it stopped. --watch keeps it resident,
re-checking the frontier on an interval; default is one pass and exit.
Freshness is printed as the embedding frontier before and after."""
from __future__ import annotations

import argparse
import time

from kawa.embeddings import (FastembedEmbedder, embed_missing, embedding_frontier,
                             extract_missing_content)
from kawa.storage.db import connect


def one_pass(batch: int) -> None:
    embedder = FastembedEmbedder()
    with connect() as conn:
        print(f"model_identity: {embedder.model_identity}")
        print(f"frontier before: {embedding_frontier(conn, embedder.model_identity)}")
        extracted = extract_missing_content(conn)
        embedded = embed_missing(conn, embedder, batch=batch)
        print(f"extracted {extracted} content rows, embedded {embedded} digests")
        print(f"frontier after: {embedding_frontier(conn, embedder.model_identity)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--watch", type=float, default=None,
                    help="stay resident, re-pass every N seconds")
    args = ap.parse_args()
    if args.watch is None:
        one_pass(args.batch)
        return 0
    while True:
        one_pass(args.batch)
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
