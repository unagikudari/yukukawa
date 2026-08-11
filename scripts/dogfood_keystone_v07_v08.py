"""Record the v0.7 and v0.8 keystone gate rounds as real Kawa Domain history.

Kawa runs its own keystone review: each round is a Plan, each lane a review Work dispatched
through the bridge, each verdict a durable Result. v0.8 cleared a two-lane distinct-vendor gate
with no surviving safety counterexample -> the keystone is frozen (#60).
"""
from __future__ import annotations

import os

from kawa.adapters import broker_bridge as bridge
from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.storage.db import connect


def review_round(k: Kawa, conn, *, plan_ref: str, objective: str, lifecycle: str, lanes):
    k.create_plan(plan_ref, "kawa", objective)
    k.set_plan_lifecycle(plan_ref, "running")
    for work_ref, target, transport, task_id, outcome, result_ref, body in lanes:
        k.derive_work(work_ref, plan_ref, "review", role_requirement="Reviewer")
        bridge.request_review(conn, work_ref=work_ref, context=objective,
                              target_agent=target, transport=transport)
        bridge.mark_dispatched(conn, work_ref, task_id)
        bridge.complete_review(k, conn, work_ref=work_ref, outcome=outcome,
                               result_ref=result_ref, body=body)
    if lifecycle != "running":
        k.set_plan_lifecycle(plan_ref, lifecycle)


def main() -> int:
    conn = connect()
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref=os.environ.get("KAWA_ORIGIN_NODE", "node-a"),
        actor_ref=os.environ.get("KAWA_ACTOR_REF", "vendor-local")))

    review_round(
        k, conn,
        plan_ref="plan-keystone-v07",
        objective="gate keystone v0.7 (carrier-from-cause + resolver-determinism)",
        lifecycle="running",
        lanes=[
            ("w-gate-v07-vendor-local", "vendor-local", "subagent", "subagent:tsk_example_v07",
             "conflicted", "subagent:tsk_example_v07",
             "vendor-local lane (v0.7) — CONFORMING on the safety core (no surviving "
             "counterexample vs either blocker-fix or node-independent E), but freeze-blocked: "
             "finding 6a (recurrence-vs-supersede carrier contradiction would silently stop "
             "recurring schedules), 6b (malformed carrier -> permanent block, needs INVALID), and "
             "freeze-obligation 1a (pin-selection artifact must exist / be node-independent)."),
            ("w-gate-v07-vendor-A", "vendor-A", "broker", "broker-task:tsk_example_v07",
             "conflicted", "github:pr60",
             "vendor-A cross-vendor lane (v0.7, GitHub-durable on PR #60) — NOT CONFORMING for freeze "
             "yet. CONVERGES with the vendor-local lane on ONE root defect: v0.7 left two derived "
             "effect-defining inputs node-locally choosable — (P0-1) intensional pin-SELECTION "
             "(S1={a,b}/S2={a,b,c} split -> different E), and (P0-2) provably-absent is a minter "
             "assertion, not a proof (crashed schedule minter). Fix as local deltas: RD0 + "
             "per-minter absence-proof; do not redesign the v0.5/v0.6 core."),
        ],
    )

    review_round(
        k, conn,
        plan_ref="plan-keystone-v08",
        objective="gate keystone v0.8 (closure principle: RD0 pin-selection + provably-absent proof)",
        lifecycle="completed",
        lanes=[
            ("w-gate-v08-vendor-local", "vendor-local", "subagent", "subagent:tsk_example_v08",
             "success", "subagent:tsk_example_v08",
             "vendor-local confirming lane (v0.8) — CONFORMING. v0.8 is a FREEZE CANDIDATE. "
             "Both P0 paths shut (S1/S2 pin-selection closed by RD0; crashed-schedule provably-"
             "absent forced to unknown-unreachable by the per-minter proof contract), closure "
             "principle total over every input to E and the firing gate, no new nonce/epoch/"
             "replication-exception. One non-blocking clarity nit on the INVALID example (fixed)."),
            ("w-gate-v08-vendor-A", "vendor-A", "broker", "broker-task:tsk_example_v08",
             "success", "github:pr60",
             "vendor-A cross-vendor confirming lane (v0.8, GitHub-durable on PR #60) — CONFORMING. "
             "v0.8 は FREEZE CANDIDATE. Re-ran both P0 traces: no surviving safety counterexample "
             "where the same causal occurrence gets a different E solely by append/replicate/"
             "compact/rebuild/observe-elsewhere. #62 §2 audit passes on the deltas. Two load-"
             "bearing freeze-RUN assertions noted: G-RD0-pin-selection (reject local admission-"
             "enrichment) and G-minter-matrix (absence-proof negative controls)."),
        ],
    )

    # The umbrella keystone plan reaches its objective: a frozen normative keystone.
    k.set_plan_lifecycle("plan-keystone", "completed", end_reason="v0.8 frozen — cleared two-lane gate (#60)")

    print("recorded v0.7 + v0.8 gate rounds; keystone plan completed. dispatch states:")
    for pref in ("plan-keystone-v07", "plan-keystone-v08"):
        st = k.plan_state(pref)
        print(f"  {pref}: {st}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
