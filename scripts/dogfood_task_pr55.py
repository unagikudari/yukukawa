"""First REAL dogfood: manage Kawa's own work through the Kawa loop.

Task: fold the PR #55 review fixes into console-read-model-v0.1.md (done on branch
spec/console-read-model, commit 18de107). This records that real work as Kawa Domain history —
Plan → research/implement/verify Work with result-driven dependencies — the first genuine
"Kawa uses Kawa to implement Kawa" history. Run once; it resets the DB to start real history.
"""
from __future__ import annotations

import os

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.projections.reducers import rebuild
from kawa.storage.db import connect

_ALL = (
    "events, event_links, event_plan, event_work, event_work_dependency, event_result, "
    "current_plans, current_work, current_work_dependency, runtime_work_occupancy"
)


def main() -> int:
    conn = connect()
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")   # begin real Kawa history at event #1
    conn.commit()

    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref=os.environ.get("KAWA_ORIGIN_NODE", "node-a"),
        actor_ref=os.environ.get("KAWA_ACTOR_REF", "vendor-local")))

    k.create_plan("plan-pr55-fold", "kawa",
                  "fold the recoverable PR#55 review findings into console-read-model, aligned with sql/0002")
    k.set_plan_lifecycle("plan-pr55-fold", "running")

    # Work graph: research → implement → verify ; plus an honest open follow-up.
    k.derive_work("w-research", "plan-pr55-fold", "research", role_requirement="Researcher")
    k.derive_work("w-implement", "plan-pr55-fold", "implement", role_requirement="Implementer")
    k.derive_work("w-verify", "plan-pr55-fold", "verify", role_requirement="Verifier")
    k.derive_work("w-recover-f6", "plan-pr55-fold", "research", role_requirement="Researcher")
    k.declare_dependency("w-implement", "w-research", "ALL")
    k.declare_dependency("w-verify", "w-implement", "ALL")
    k.declare_dependency("w-recover-f6", "w-research", "ALL")

    # research: recovered 5 of 6 findings; the 6th was truncated in the broker note -> CONFLICTED
    assert k.work_next("Researcher") == {"work_ref": "w-research", "plan_ref": "plan-pr55-fold",
                                         "work_kind": "research", "role_requirement": "Researcher"}
    k.record_result("w-research", "conflicted", "broker-task:tsk_example",
                    "recovered a-1/a-2/c-1/c-2/c-3; 6th finding truncated in broker note")

    # a conflicted result still unblocks the dependents (resolve-but-flag, #53 §4)
    assert k.work_state("w-implement") == "ready"
    k.record_result("w-implement", "success", "git:18de107",
                    "folded a-1/a-2/c-1/c-2/c-3 into console-read-model-v0.1.md")

    assert k.work_state("w-verify") == "ready"
    k.record_result("w-verify", "success", "verify:doc-matches-sql0002",
                    "doc DDL aligns with sql/0002; phase0 suite 11/11 green")

    # honest open item: recovering the truncated 6th finding is READY but not yet done
    assert k.work_state("w-recover-f6") == "ready"

    # report
    print("=== current_plans ===")
    with conn.cursor() as cur:
        cur.execute("SELECT plan_ref, lifecycle FROM current_plans")
        for row in cur.fetchall():
            print(" ", row)
        print("=== current_work (work_ref, kind, execution, dep_total, dep_satisfied, dep_conflicted) ===")
        cur.execute("SELECT work_ref, work_kind, execution, dependency_total, dependency_satisfied, "
                    "dependency_conflicted FROM current_work ORDER BY work_ref")
        for row in cur.fetchall():
            print(" ", row)
    print("open Work (work.next Researcher):", k.work_next("Researcher"))

    n = rebuild(conn)
    print(f"\nrebuild replayed {n} real events; projections reproduced ✓")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
