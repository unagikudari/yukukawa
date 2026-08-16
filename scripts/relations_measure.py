"""relations VIEW benchmark + parity measurement on the REAL dogfood log (#141 rev 3 §1/§6).

Records two signed Observations (the #122 measure_* pattern — pytest is fenced OFF the
dogfood DB by 12A, so real-log measurement is a read-only script + Observation, never a
pytest fixture):

- relations_view_benchmark  : depth-2 traversal p95 over N runs (ms). The eager-table
  promotion gate: p95 > 10ms promotes; otherwise the VIEW stands. Either way the decision
  cites this Observation.
- relations_parity          : projection answers vs direct typed-table derivations
  (derives/depends_on/evidences edge sets must match exactly; scar-tissue origins are
  classified, never skipped silently).

Usage: KAWA_DSN=dbname=kawa .venv/bin/python scripts/relations_measure.py [--runs 100]
Exit:  0 measured (both Observations recorded) · 2 error · 3 parity FAILED (Observation
       still recorded — a loud number, not a crash)
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time

from kawa.application.services import Kawa
from kawa.domain.credential import load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.relations import traverse
from kawa.storage.db import connect

PROMOTION_GATE_MS = 10.0


def main() -> int:
    runs = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 100
    conn = connect()
    cred = load_or_create_local_node(os.path.expanduser("~/.kawa/node_credential.json"))
    kawa = Kawa(conn, identity=IdentityContext.from_local_node(
        cred, actor_ref="relations-measure"))

    with conn.cursor() as cur:
        cur.execute("SELECT plan_ref FROM current_plans ORDER BY plan_ref")
        plans = [r[0] for r in cur.fetchall()]

    # --- benchmark: depth-2 from every plan subject, p95 over `runs` samples ---
    samples: list[float] = []
    i = 0
    while len(samples) < runs:
        plan = plans[i % len(plans)]
        t0 = time.perf_counter()
        traverse(conn, "plan", plan, depth=2)
        samples.append((time.perf_counter() - t0) * 1000)
        i += 1
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    promote = p95 > PROMOTION_GATE_MS

    # --- parity: projection vs direct typed-table derivation (latest per 5-tuple) ---
    with conn.cursor() as cur:
        mismatches: list[str] = []
        cur.execute("SELECT source_id, target_id FROM current_relations "
                    "WHERE relation_kind='derives'")
        proj = set(cur.fetchall())
        cur.execute("SELECT DISTINCT ew.plan_ref, ew.work_ref FROM event_work ew")
        direct = set(cur.fetchall())
        if proj != direct:
            mismatches.append(f"derives: proj^direct={len(proj ^ direct)}")
        cur.execute("SELECT source_id, target_id FROM current_relations "
                    "WHERE relation_kind='depends_on'")
        proj = set(cur.fetchall())
        cur.execute("SELECT DISTINCT work_ref, dependency_work_ref FROM event_work_dependency")
        direct = set(cur.fetchall())
        if proj != direct:
            mismatches.append(f"depends_on: proj^direct={len(proj ^ direct)}")
        cur.execute("SELECT source_id, target_id FROM current_relations "
                    "WHERE relation_kind='evidences'")
        proj = set(cur.fetchall())
        cur.execute("SELECT DISTINCT result_ref, work_ref FROM event_result")
        direct = set(cur.fetchall())
        if proj != direct:
            mismatches.append(f"evidences: proj^direct={len(proj ^ direct)}")
        # scar tissue classified, never silently skipped (rev 3 §6)
        cur.execute("SELECT origin_node, count(*) FROM events "
                    "WHERE origin_node IN ('local','test') GROUP BY 1")
        scar = {r[0]: r[1] for r in cur.fetchall()}
    parity_ok = not mismatches

    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    kawa.record_observation(
        "relations_view_benchmark", value_number=round(p95, 3), method="metric_read",
        source_ref="kawa://current_relations", fetched_at=ts,
        content_digest="sha256:" + __import__("hashlib").sha256(
            json.dumps(sorted(samples)).encode()).hexdigest(),
        source_revision=(f"depth=2 runs={runs} p95_ms={p95:.3f} gate_ms={PROMOTION_GATE_MS} "
                         f"promote_eager_table={promote} plans={len(plans)}"))
    kawa.record_observation(
        "relations_parity", value_bool=parity_ok, method="metric_read",
        source_ref="kawa://current_relations", fetched_at=ts,
        content_digest="sha256:" + __import__("hashlib").sha256(
            json.dumps(mismatches).encode()).hexdigest(),
        source_revision=(f"mismatches={mismatches or 'none'} "
                         f"scar_tissue_origins={json.dumps(scar, sort_keys=True)} (classified: "
                         "backfill-signed, sealed origins — represented like any other edge)"))
    conn.commit()
    print(f"benchmark: depth-2 p95={p95:.3f}ms over {runs} runs -> "
          f"{'PROMOTE eager table' if promote else 'VIEW stands'} (gate {PROMOTION_GATE_MS}ms)")
    print(f"parity: {'OK' if parity_ok else 'FAILED ' + str(mismatches)} | scar tissue: {scar}")
    return 0 if parity_ok else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
