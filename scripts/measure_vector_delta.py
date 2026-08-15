"""Step 11B acceptance: re-run 11A's corpus WITH the vector class and record the
measured delta (#122: "the step's success claim is the measured delta, recorded
as Observations, not the feature's existence").

Usage:
  KAWA_DSN=dbname=kawa python scripts/measure_vector_delta.py corpus.json \
      --baseline corpus/recall-report-v1.json --adjudication merged.json \
      [--out report.json] [--record]

Also machine-checks the ZERO-SHADOWING negative control (#122 BC-4/5): every
label the structural pipeline hit in the baseline must still be hit now. Any
regression fails the run loudly — vector may only ADD reach, never displace.
--record emits per-class `retrieval_recall_delta` Observations plus one
`retrieval_vector_no_shadowing` Observation, source-bound to the corpus digest,
with the answering model identity in source_revision."""
from __future__ import annotations

import argparse
import datetime
import json
import os

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.embeddings import embedding_frontier
from kawa.retrieval_eval import CLASSES, corpus_digest, measure
from kawa.storage.db import connect


def hit_keys(report: dict) -> set[str]:
    """Every (query, label) pair the pipeline RESOLVED — the complement of the
    misses, over the same corpus. Used for the no-shadowing paired check."""
    missed = {m["miss_key"] for c in report["classes"].values() for m in c["misses"]}
    all_keys = set(report["_all_keys"])
    return all_keys - missed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--adjudication")
    ap.add_argument("--out")
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()

    raw = open(args.corpus, "rb").read()
    corpus = json.loads(raw)
    baseline = json.loads(open(args.baseline, encoding="utf-8").read())
    adjudication = json.loads(open(args.adjudication, encoding="utf-8").read()) \
        if args.adjudication else None
    if baseline.get("corpus_digest") != corpus_digest(raw):
        raise SystemExit(f"baseline was measured against a DIFFERENT corpus "
                         f"({baseline.get('corpus_digest')} != {corpus_digest(raw)}) — "
                         "the paired delta would be meaningless")

    all_keys = [f"{q['query_id']}::{label}" for q in corpus["queries"] for label in q["labels"]]
    from kawa.embeddings import FastembedEmbedder
    live = FastembedEmbedder()          # textual queries get the live model too
    with connect() as conn:
        report = measure(conn, corpus, raw, adjudication, embedder=live)
        frontier = embedding_frontier(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT model_identity FROM content_embedding "
                        "GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1")
            row = cur.fetchone()
        model = row[0] if row else "(no index)"

        report["_all_keys"] = all_keys
        baseline["_all_keys"] = all_keys
        before_hits, after_hits = hit_keys(baseline), hit_keys(report)
        regressions = sorted(before_hits - after_hits)
        newly_hit = sorted(after_hits - before_hits)

        delta = {}
        for c in CLASSES:
            b = baseline["classes"][c]["micro_recall"]
            a = report["classes"][c]["micro_recall"]
            delta[c] = {"before": b, "after": a,
                        "delta": (a - b) if (a is not None and b is not None) else None}
        out = {
            "corpus_digest": report["corpus_digest"],
            "verdict_after": report["verdict"],
            "model_identity": model,
            "embedding_frontier": frontier,
            "per_class": delta,
            "newly_hit": newly_hit,
            "no_shadowing": {"ok": not regressions, "regressions": regressions},
        }

        if args.record:
            kawa = Kawa(conn, identity=IdentityContext.from_credential_file(
                actor_ref="recall-harness"))
            fetched = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            dg = report["corpus_digest"]
            src = f"file://{os.path.abspath(args.corpus)}"
            rev_suffix = f"model={model} no_shadowing={not regressions}"
            ids = []
            for c, d in delta.items():
                if d["delta"] is None:
                    continue
                ev = kawa.record_observation(
                    "retrieval_recall_delta", value_number=d["delta"],
                    method="command_exit", source_ref=src,
                    source_revision=f"class={c} before={d['before']:.4f} "
                                    f"after={d['after']:.4f} {rev_suffix}",
                    content_digest=dg, fetched_at=fetched)
                ids.append(ev.event_id)
            ev = kawa.record_observation(
                "retrieval_vector_no_shadowing", value_bool=not regressions,
                method="command_exit", source_ref=src,
                source_revision=f"regressions={len(regressions)} newly_hit={len(newly_hit)}",
                content_digest=dg, fetched_at=fetched)
            ids.append(ev.event_id)
            out["observation_ids"] = ids

    text = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    if regressions:
        print(f"\nNO-SHADOWING VIOLATION: {len(regressions)} formerly-hit labels lost")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
