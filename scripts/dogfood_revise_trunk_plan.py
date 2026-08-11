"""Plan revision (#59 §1.4, live): the review + #59 changed the merge's execution basis.

Record the PR#58 review Result (refuted/block) and insert the #59 8-seam design gate as a new
dependency of the merge. The merge Work revalidates rather than proceeds — Work waits for
evidence, and cancellation/revision changes future standing, not past history.
"""
from __future__ import annotations

import os

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.storage.db import connect


def main() -> int:
    conn = connect()
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref=os.environ.get("KAWA_ORIGIN_NODE", "node-a"),
        actor_ref=os.environ.get("KAWA_ACTOR_REF", "vendor-local")))

    # The review came back: refuted / block_until_fixes. It ARRIVED (dependency satisfied) but its
    # content is "block" — which our conflated dependency_state can't cleanly express. That very
    # awkwardness is #59 seam #3 showing up in the dogfood: dependency-arrival != input-standing.
    k.record_result("w-review-58", "conflicted", "broker-task:tsk_example",
                    "refuted: block_until_fixes; details lossy in broker note; self-audit found c/e/f")

    # Plan revision: insert the #59 design gate; the merge now also depends on it.
    k.derive_work("w-resolve-seams", "plan-phase0-trunk", "plan", role_requirement="Planner")
    k.declare_dependency("w-merge-58", "w-resolve-seams", "ALL")

    print("plan-phase0-trunk after revision:")
    for w in ("w-review-58", "w-verify-58", "w-resolve-seams", "w-merge-58"):
        print(f"  {w}: {k.work_state(w)}")
    print("work.next(Planner):", k.work_next("Planner"))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
