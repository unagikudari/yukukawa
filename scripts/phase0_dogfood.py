"""Phase 0 dogfood loop — the canonical #56 §13 scenario, end to end.

    Plan → Work(implement) + Work(review, depends_on implement)
         → work.next(Implementer) → Result → review becomes READY
         → work.next(Reviewer)    → Result → Plan satisfied

Then demonstrate that projections are disposable: rebuild from the Event log and show the
current state is identical. Run:  python scripts/phase0_dogfood.py
"""
from __future__ import annotations

import os

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.projections.reducers import rebuild
from kawa.storage.db import connect

_ALL_TABLES = (
    "events, event_links, event_plan, event_work, event_work_dependency, event_result, "
    "current_plans, current_work, current_work_dependency, runtime_work_occupancy"
)


def reset(conn) -> None:  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL_TABLES}")
    conn.commit()


def snapshot(conn) -> dict[str, object]:  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT plan_ref, lifecycle, end_reason FROM current_plans ORDER BY plan_ref")
        plans = cur.fetchall()
        cur.execute("SELECT work_ref, execution, dependency_total, dependency_satisfied "
                    "FROM current_work ORDER BY work_ref")
        work = cur.fetchall()
    return {"plans": plans, "work": work}


def main() -> int:
    conn = connect()
    reset(conn)
    k = Kawa(conn, identity=IdentityContext.from_credential_file(
        actor_ref=os.environ.get("KAWA_ACTOR_REF", "vendor-local")))

    print("1. create Plan")
    k.create_plan("plan-dogfood-1", "kawa", "prove the Phase 0 loop")
    k.set_plan_lifecycle("plan-dogfood-1", "running")

    print("2. derive Work: implement, review(depends_on implement)")
    k.derive_work("work-impl", "plan-dogfood-1", "implement", role_requirement="Implementer")
    k.derive_work("work-review", "plan-dogfood-1", "review", role_requirement="Reviewer")
    k.declare_dependency("work-review", "work-impl", "ALL")

    print(f"   work-impl   execution = {k.work_state('work-impl')}")
    print(f"   work-review execution = {k.work_state('work-review')}  (blocked on impl)")
    assert k.work_state("work-review") == "blocked", "review must block until impl result"

    print("3. work.next(Implementer) → run it → record Result(success)")
    nxt = k.work_next("Implementer")
    assert nxt is not None and nxt["work_ref"] == "work-impl", nxt
    k.record_result("work-impl", "success", "result-impl-1", "implementation done")

    print(f"   work-review execution = {k.work_state('work-review')}  (now ready — evidence unblocked it)")
    assert k.work_state("work-review") == "ready", "review must become ready after impl result"

    print("4. work.next(Reviewer) → run it → record Result(success)")
    nxt = k.work_next("Reviewer")
    assert nxt is not None and nxt["work_ref"] == "work-review", nxt
    k.record_result("work-review", "success", "result-review-1", "review passed")

    print("5. Plan satisfied → lifecycle ended/completed")
    k.set_plan_lifecycle("plan-dogfood-1", "ended", "completed")
    assert k.plan_state("plan-dogfood-1") == ("ended", "completed")

    before = snapshot(conn)
    print("\nprojections (current):")
    print("  plans:", before["plans"])
    print("  work: ", before["work"])

    print("\n6. projections are disposable — DROP-equivalent + replay the Event log")
    n = rebuild(conn)
    after = snapshot(conn)
    assert after == before, "rebuild must reproduce identical projections"
    print(f"   replayed {n} events; projections identical after rebuild ✓")

    print("\nOK — Kawa Phase 0 loop runs end to end.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
