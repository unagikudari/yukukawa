"""Phase 0 end-to-end integration test — requires the local `kawa` database.

Skips cleanly if the DB is unavailable, so `pytest` stays green in environments without it.
"""
from __future__ import annotations

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.projections.reducers import rebuild

psycopg = pytest.importorskip("psycopg")

_ALL = (
    "events, event_links, event_plan, event_work, event_work_dependency, event_result, "
    "current_plans, current_work, current_work_dependency, runtime_work_occupancy"
)


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    from kawa.storage.db import connect
    try:
        c = connect()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"kawa DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    yield c
    c.close()


def _snapshot(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT plan_ref, lifecycle, end_reason FROM current_plans ORDER BY plan_ref")
        plans = cur.fetchall()
        cur.execute("SELECT work_ref, execution, dependency_total, dependency_satisfied, "
                    "dependency_conflicted FROM current_work ORDER BY work_ref")
        work = cur.fetchall()
    return {"plans": plans, "work": work}


def test_result_driven_readiness_loop(conn) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p1", "kawa", "loop")
    k.derive_work("impl", "p1", "implement", role_requirement="Implementer")
    k.derive_work("review", "p1", "review", role_requirement="Reviewer")
    k.declare_dependency("review", "impl", "ALL")

    assert k.work_state("impl") == "ready"
    assert k.work_state("review") == "blocked"           # blocked on evidence, not on an agent

    assert k.work_next("Implementer") == {"work_ref": "impl", "plan_ref": "p1",
                                          "work_kind": "implement", "role_requirement": "Implementer"}
    assert k.work_next("Reviewer") is None               # nothing ready for reviewer yet

    k.record_result("impl", "success", "r-impl")
    assert k.work_state("review") == "ready"             # Result unblocked the dependent Work
    assert k.work_next("Reviewer") is not None and k.work_next("Reviewer")["work_ref"] == "review"

    k.record_result("review", "success", "r-review")
    assert k.work_state("review") == "finished"


def test_conflicted_result_still_satisfies_but_flags(conn) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p2", "kawa", "conflict")
    k.derive_work("a", "p2", "implement")
    k.derive_work("b", "p2", "review")
    k.declare_dependency("b", "a", "ALL")
    k.record_result("a", "conflicted", "r-a")
    assert k.work_state("b") == "ready"                  # conflicted resolves the dependency (#53 §4)
    with conn.cursor() as cur:
        cur.execute("SELECT dependency_conflicted FROM current_work WHERE work_ref='b'")
        assert cur.fetchone()[0] == 1                    # ...but the conflict is surfaced, not hidden


def test_failed_dependency_blocks(conn) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p3", "kawa", "fail")
    k.derive_work("x", "p3", "implement")
    k.derive_work("y", "p3", "review")
    k.declare_dependency("y", "x", "ALL")
    k.record_result("x", "failure", "r-x")
    assert k.work_state("y") == "blocked"                # a failed dependency does not make y ready


def test_projections_are_disposable(conn) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p4", "kawa", "rebuild")
    k.derive_work("w", "p4", "implement")
    k.record_result("w", "success", "r-w")
    before = _snapshot(conn)
    n = rebuild(conn)                                    # DROP-equivalent + replay
    assert n >= 3
    assert _snapshot(conn) == before                     # identical after rebuild
