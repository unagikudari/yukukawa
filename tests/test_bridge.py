"""Bridge test — the request context and the FULL result live durably in Kawa (the #56/#59 win).

Skips if the dedicated TEST database `kawa_test_a` is unavailable. Like test_e2e, the fixture
TRUNCATEs — so it reads only KAWA_TEST_DSN_A and ignores KAWA_DSN (the runtime DSN). This file
was the hole #91 missed: on 2026-08-12 a plain `pytest` run reached this fixture with no KAWA_DSN
set and truncated the live dogfood log. Every DB-touching test fixture must use this seam.
"""
from __future__ import annotations

import os

import pytest

from kawa.adapters import broker_bridge as bridge
from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext

psycopg = pytest.importorskip("psycopg")

_ALL = (
    "events, event_links, event_plan, event_work, event_work_dependency, event_result, "
    "current_plans, current_work, current_work_dependency, runtime_work_occupancy, work_dispatch"
)


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a"),
                            autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"kawa test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    yield c
    c.close()


def test_dispatch_bridge_keeps_request_and_result_in_kawa(conn) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p", "kawa", "bridge")
    k.derive_work("w-rev", "p", "review", role_requirement="Reviewer")

    long_context = "please review X\n" * 500  # would be at risk of truncation in a transport note
    bridge.request_review(conn, work_ref="w-rev", context=long_context, target_agent="vendor-B")
    assert bridge.pending_dispatches(conn) == [("w-rev", long_context, "vendor-B")]

    bridge.mark_dispatched(conn, "w-rev", "broker-task:abcd1234")
    assert bridge.dispatch_status(conn, "w-rev") == ("dispatched", "broker-task:abcd1234")
    assert bridge.pending_dispatches(conn) == []  # no longer pending

    long_body = "VERDICT: conforming.\n" + ("detail line\n" * 800)  # full result body
    bridge.complete_review(k, conn, work_ref="w-rev", outcome="success",
                           result_ref="broker-task:abcd1234", body=long_body)

    # durable win: the FULL body is a Kawa Result Event, not a truncatable note
    with conn.cursor() as cur:
        cur.execute("SELECT summary FROM event_result WHERE work_ref='w-rev'")
        stored = cur.fetchone()[0]
    assert stored == long_body
    assert k.work_state("w-rev") == "finished"
    assert bridge.dispatch_status(conn, "w-rev") == ("completed", "broker-task:abcd1234")


def test_dispatch_result_drives_downstream_readiness(conn) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p2", "kawa", "bridge2")
    k.derive_work("w-a", "p2", "review", role_requirement="Reviewer")
    k.derive_work("w-b", "p2", "verify", role_requirement="Verifier")
    k.declare_dependency("w-b", "w-a", "ALL")
    bridge.request_review(conn, work_ref="w-a", context="c", target_agent="vendor-B")
    bridge.mark_dispatched(conn, "w-a", "t1")
    assert k.work_state("w-b") == "blocked"
    bridge.complete_review(k, conn, work_ref="w-a", outcome="success", result_ref="t1", body="ok")
    assert k.work_state("w-b") == "ready"  # a bridge-recorded Result unblocks downstream Work
