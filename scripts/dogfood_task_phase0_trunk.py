"""Second real dogfood: drive "review & land Phase 0 impl (PR #58) on trunk" through Kawa.

Accumulates onto the real Kawa history (no reset). Sets up the review → verify → merge Work
graph; results get recorded as the adversarial review returns and the merge happens.
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

    k.create_plan("plan-phase0-trunk", "kawa",
                  "review and land the Phase 0 implementation (PR #58) on trunk")
    k.set_plan_lifecycle("plan-phase0-trunk", "running")

    k.derive_work("w-review-58", "plan-phase0-trunk", "review", role_requirement="Reviewer")
    k.derive_work("w-verify-58", "plan-phase0-trunk", "verify", role_requirement="Verifier")
    k.derive_work("w-merge-58", "plan-phase0-trunk", "implement", role_requirement="Implementer")
    k.declare_dependency("w-verify-58", "w-review-58", "ALL")
    k.declare_dependency("w-merge-58", "w-verify-58", "ALL")

    print("plan-phase0-trunk created. Work states:")
    for w in ("w-review-58", "w-verify-58", "w-merge-58"):
        print(f"  {w}: {k.work_state(w)}")
    print("work.next(Reviewer):", k.work_next("Reviewer"))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
