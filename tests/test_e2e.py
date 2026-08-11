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


def test_execution_unknown_does_not_satisfy_or_proceed(conn) -> None:  # type: ignore[no-untyped-def]
    """#53 §10 / Phase-1 exit: an `execution_unknown` Result must NOT satisfy a dependency or let
    downstream Work proceed — unknown is not success, so a consequential effect cannot be blindly
    retried or treated as done. Verification is required first."""
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("pu", "kawa", "execution-unknown safety")
    k.derive_work("act", "pu", "implement", role_requirement="Implementer")   # a consequential effect
    k.derive_work("next", "pu", "implement", role_requirement="Implementer")
    k.declare_dependency("next", "act", "ALL")

    # the effect's outcome is UNKNOWN (crash-after-effect-before-Result), not success
    k.record_result("act", "execution_unknown", "r-act-unknown")

    with conn.cursor() as cur:
        cur.execute("SELECT dependency_state FROM current_work_dependency "
                    "WHERE work_ref='next' AND dependency_work_ref='act'")
        dep_state = cur.fetchone()[0]
        cur.execute("SELECT execution FROM current_work WHERE work_ref='act'")
        act_exec = cur.fetchone()[0]
        cur.execute("SELECT execution FROM current_work WHERE work_ref='next'")
        next_exec = cur.fetchone()[0]

    assert dep_state == "pending", f"unknown must NOT satisfy the dependency, got {dep_state}"
    assert act_exec == "execution_unknown", f"the effect Work stays execution_unknown, got {act_exec}"
    assert next_exec != "ready", f"downstream must NOT become actionable on unknown, got {next_exec}"


def test_agent_replacement_resumes_from_kawa_state(conn) -> None:  # type: ignore[no-untyped-def]
    """#72 Phase-2 exit: agent replacement / runtime loss does not erase organizational continuity.
    A fresh runtime holding NO prior context resumes the workflow purely from durable Kawa state via
    work.next — no agent-to-agent messaging, no in-runtime queue. Kawa holds the work; runtimes pass
    through."""
    # Runtime A sets up the workflow, then disappears (it held no workflow state — all durable in Kawa).
    a = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="runtime-a", actor_ref="agent-a"))
    a.create_plan("pr", "kawa", "resume-continuity")
    a.derive_work("build", "pr", "implement", role_requirement="Implementer")
    a.derive_work("check", "pr", "review", role_requirement="Reviewer")
    a.declare_dependency("check", "build", "ALL")
    del a  # runtime A gone

    # Runtime B — brand new, no prior context — discovers the actionable Work from durable state.
    b = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="runtime-b", actor_ref="agent-b"))
    nxt = b.work_next("Implementer")
    assert nxt is not None and nxt["work_ref"] == "build"     # resumed purely from Kawa state
    b.record_result("build", "success", "r-build")
    del b  # runtime B gone mid-workflow

    # Runtime C — also new — picks up the now-unblocked downstream Work. Result, not a message, routed it.
    c = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="runtime-c", actor_ref="agent-c"))
    nxt = c.work_next("Reviewer")
    assert nxt is not None and nxt["work_ref"] == "check"     # continuity survived two runtime losses
    c.record_result("check", "success", "r-check")
    assert c.work_state("check") == "finished"


def test_console_renders_from_live_projections(conn) -> None:  # type: ignore[no-untyped-def]
    """Phase 3: the Console renders from LIVE projections, never a static snapshot — a change is
    reflected on the next render."""
    from kawa.console.render import render_page
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("pc", "kawa", "console")
    k.derive_work("wc", "pc", "implement", role_requirement="Implementer")
    page1 = render_page(conn)
    assert "pc" in page1 and "wc" in page1 and "READY" in page1   # live current_work.execution='ready'
    k.record_result("wc", "success", "r-wc")                     # change the live state
    page2 = render_page(conn)
    assert "finished" in page2 and page2 != page1                # re-read reflects it (no snapshot)


def test_console_shows_dispatch_from_live_work_dispatch(conn) -> None:  # type: ignore[no-untyped-def]
    """Phase 3: the Runtime & Dispatch section renders from live work_dispatch (how Work was routed
    to runtimes/lanes)."""
    from kawa.adapters import broker_bridge as bridge
    from kawa.console.render import render_page
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("pd", "kawa", "dispatch")
    k.derive_work("wd", "pd", "review", role_requirement="Reviewer")
    bridge.request_review(conn, work_ref="wd", context="ctx", target_agent="vendor-A", transport="broker")
    page = render_page(conn)
    assert "Runtime &amp; Dispatch" in page and "vendor-A" in page and "broker" in page
