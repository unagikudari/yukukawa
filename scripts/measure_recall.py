"""Run the #122 recall gate: corpus -> harness -> verdict JSON (+ Observations with --record).

Usage:
  KAWA_DSN=dbname=kawa python scripts/measure_recall.py corpus.json [--adjudication merged.json]
      [--out report.json] [--blind-export blind.json] [--record]

The verdict is COMPUTED (GO | NO_GO | PENDING_ADJUDICATION | INVALID_CORPUS) — the Result
for w-step11 may only cite it (kawa.retrieval_eval.guard_result refuses divergence, r1 (j)).
--blind-export writes the BC-2 double-blind package (misses without the author's causes)
for independent re-diagnosis. --record emits per-class `retrieval_recall` Observations and
one `retrieval_gate_verdict` Observation, all source-bound to the corpus file digest."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.retrieval_eval import blind_export, corpus_digest, measure
from kawa.storage.db import connect


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--adjudication")
    ap.add_argument("--out")
    ap.add_argument("--blind-export")
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()

    raw = open(args.corpus, "rb").read()
    corpus = json.loads(raw)
    adjudication = json.loads(open(args.adjudication, encoding="utf-8").read()) \
        if args.adjudication else None

    with connect() as conn:
        report = measure(conn, corpus, raw, adjudication)
        if args.blind_export:
            with open(args.blind_export, "w", encoding="utf-8") as f:
                json.dump(blind_export(report), f, indent=2, ensure_ascii=False)
        if args.record and report["verdict"] not in ("INVALID_CORPUS",):
            kawa = Kawa(conn, identity=IdentityContext.from_local_runtime(
                node_ref=os.environ.get("KAWA_NODE", "local"), actor_ref="recall-harness"))
            fetched = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            dg = corpus_digest(raw)
            src = f"file://{os.path.abspath(args.corpus)}"
            for cls, c in report["classes"].items():
                if c["micro_recall"] is not None:
                    ev = kawa.record_observation(
                        "retrieval_recall", value_number=c["micro_recall"],
                        method="command_exit", source_ref=src,
                        source_revision=f"class={cls} n={c['n']} verdict={report['verdict']}",
                        content_digest=dg, fetched_at=fetched)
                    report.setdefault("observation_ids", []).append(ev.event_id)
            ev = kawa.record_observation(
                "retrieval_gate_verdict", value_text=report["verdict"],
                method="command_exit", source_ref=src,
                source_revision=f"bar={report['bar']} min_n={report['min_class_n']}",
                content_digest=dg, fetched_at=fetched)
            report.setdefault("observation_ids", []).append(ev.event_id)
    out = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
    print(out[:2000])
    print(f"\nverdict: {report['verdict']}")
    return 0 if report["verdict"] in ("GO", "NO_GO") else 1


if __name__ == "__main__":
    sys.exit(main())
