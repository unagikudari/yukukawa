"""Step 12C — the resident pull loop's tick (#129 rev 3).

What is proven here, against the fenced test DB:
- a newly-eligible Work is surfaced on the next tick: signed-path Observation
  (`work_surfaced`) + status file entry, propagation computed from
  `events.recorded_at` of the completing event (F2 semantics)
- a second tick does NOT re-surface (state file), but a Work that leaves ready
  and becomes ready again DOES (prune-and-return is a new eligibility)
- a bridge failure lands in `bridge_error` and never fails the tick (R5/F3)
- the status file is written on every tick and carries the bridge deprecation
- lost state ⇒ duplicate surfacing, never silence (at-least-once, kill-anywhere)
"""
from __future__ import annotations

import json
import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from scripts.supervisor import load_state, run_tick

psycopg = pytest.importorskip("psycopg")

_ALL = (
    "content_embedding, event_content, events, event_links, event_link, event_observation, event_claim, event_plan, "
    "event_work, event_work_dependency, event_work_retired, event_result, current_claim_standing, "
    "current_plans, current_work, current_work_dependency, runtime_work_occupancy, work_dispatch"
)


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a"), autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def k(conn):  # type: ignore[no-untyped-def]
    return Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))


def _tick(conn, k, tmp_path, state, *, bridge_fn=None, bridge_label="none", tick_no=1):  # type: ignore[no-untyped-def]
    return run_tick(conn, kawa=k, node_ref="test", state=state,
                    status_file=str(tmp_path / "supervisor.status"),
                    state_file=str(tmp_path / "supervisor.state.json"),
                    bridge_fn=bridge_fn, bridge_label=bridge_label,
                    tick_no=tick_no, policy_dgst="sha256:test")


def _observations(conn, predicate="work_surfaced"):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT source_ref, value_number FROM event_observation "
                    "WHERE predicate = %s ORDER BY event_id", (predicate,))
        return cur.fetchall()


def test_new_ready_work_is_surfaced_with_measured_propagation(conn, k, tmp_path):  # type: ignore[no-untyped-def]
    k.create_plan("p", "kawa", "12c")
    k.derive_work("w-a", "p", "implement", role_requirement="Implementer")
    state = load_state(str(tmp_path / "supervisor.state.json"))

    status = _tick(conn, k, tmp_path, state)

    assert status["ok"] and status["ready"] == ["w-a"]
    assert [s["work_ref"] for s in status["surfaced_this_tick"]] == ["w-a"]
    # F2: propagation from events.recorded_at of the completing event — small and non-negative
    delta = status["surfaced_this_tick"][0]["propagation_s"]
    assert delta is not None and 0 <= delta < 60
    obs = _observations(conn)
    assert [r[0] for r in obs] == ["kawa://current_work/w-a"]
    written = json.loads((tmp_path / "supervisor.status").read_text())
    assert written["ready"] == ["w-a"] and written["bridge"] == "none"


def test_second_tick_does_not_resurface(conn, k, tmp_path):  # type: ignore[no-untyped-def]
    k.create_plan("p", "kawa", "12c")
    k.derive_work("w-a", "p", "implement")
    state = load_state(str(tmp_path / "supervisor.state.json"))
    _tick(conn, k, tmp_path, state)

    status2 = _tick(conn, k, tmp_path, state, tick_no=2)

    assert status2["surfaced_this_tick"] == []
    assert len(_observations(conn)) == 1
    # ...but the status file is STILL written every tick (R5: always)
    assert json.loads((tmp_path / "supervisor.status").read_text())["tick"] == 2


def test_leaving_ready_prunes_state_and_re_eligibility_resurfaces(conn, k, tmp_path):  # type: ignore[no-untyped-def]
    k.create_plan("p", "kawa", "12c")
    k.derive_work("w-a", "p", "implement")
    state = load_state(str(tmp_path / "supervisor.state.json"))
    _tick(conn, k, tmp_path, state)

    k.record_result("w-a", "success", "result://done")   # leaves ready
    status2 = _tick(conn, k, tmp_path, state, tick_no=2)
    assert status2["ready"] == [] and "w-a" not in state["surfaced"]

    k.derive_work("w-a2", "p", "implement")              # a NEW eligibility surfaces
    status3 = _tick(conn, k, tmp_path, state, tick_no=3)
    assert [s["work_ref"] for s in status3["surfaced_this_tick"]] == ["w-a2"]


def test_bridge_failure_never_fails_the_tick(conn, k, tmp_path):  # type: ignore[no-untyped-def]
    k.create_plan("p", "kawa", "12c")
    k.derive_work("w-a", "p", "implement")
    state = load_state(str(tmp_path / "supervisor.state.json"))

    def broken_bridge(items):  # type: ignore[no-untyped-def]
        raise ConnectionError("broker is down")

    status = _tick(conn, k, tmp_path, state, bridge_fn=broken_bridge,
                   bridge_label="deprecated-active")

    assert status["ok"] is True
    assert status["bridge"] == "deprecated-active"       # F3: visible inertia
    assert status["bridge_error"].startswith("ConnectionError")
    assert len(_observations(conn)) == 1                 # the surfacing still happened


def test_lost_state_duplicates_but_never_silences(conn, k, tmp_path):  # type: ignore[no-untyped-def]
    k.create_plan("p", "kawa", "12c")
    k.derive_work("w-a", "p", "implement")
    state = load_state(str(tmp_path / "supervisor.state.json"))
    _tick(conn, k, tmp_path, state)

    fresh_state = load_state(str(tmp_path / "gone.state.json"))   # state lost
    status2 = _tick(conn, k, tmp_path, fresh_state, tick_no=2)

    # at-least-once: the duplicate is the accepted cost; silence is the failure mode
    assert [s["work_ref"] for s in status2["surfaced_this_tick"]] == ["w-a"]
    assert len(_observations(conn)) == 2


def test_iso_renders_utc_regardless_of_session_timezone():  # type: ignore[no-untyped-def]
    """The first production tick stamped a JST wall time with a Z (t_eligible
    apparently 9h in the future). _iso must convert aware datetimes to UTC."""
    import datetime as dt
    from scripts.supervisor import _iso
    jst = dt.timezone(dt.timedelta(hours=9))
    aware = dt.datetime(2026, 8, 15, 10, 34, 24, tzinfo=jst)   # = 01:34:24Z
    assert _iso(aware) == "2026-08-15T01:34:24Z"


def test_tick_never_leaves_a_transaction_open(conn, k, tmp_path):  # type: ignore[no-untyped-def]
    """A resident loop must end every tick with the connection idle — NOT
    idle-in-transaction (found live 2026-08-17: a no-new-work tick held its read
    txn open for hours, pinning vacuum xmin and wedging a projection rebuild's
    ACCESS EXCLUSIVE, which then queued all later tick reads: full standstill)."""
    from psycopg import pq
    state = load_state(str(tmp_path / "supervisor.state.json"))
    _tick(conn, k, tmp_path, state)                      # no new work: pure-read tick
    assert conn.info.transaction_status == pq.TransactionStatus.IDLE
    k.create_plan("p-txn", "kawa", "txn hygiene")
    k.derive_work("w-txn", "p-txn", "implement")
    _tick(conn, k, tmp_path, state, tick_no=2)           # surfacing tick
    assert conn.info.transaction_status == pq.TransactionStatus.IDLE
