"""#166 order-tolerant reducers — permutation property tests (design rev 2, APPROVED).

The guarantee under test: current_* projections are PURE FUNCTIONS of the payload fact
set — for any admitted event log, ANY reduce order produces identical projections. The
permutations here deliberately exceed what admission can deliver (per-origin contiguity
limits real interleavings): the reducers must be confluent over raw order, so no future
delivery path (replication interleavings, generation-filtered replay, archive import)
can resurrect the 2026-08-17 class. Fixtures follow the real w-b/p2 episode."""
from __future__ import annotations

import os
from itertools import permutations

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.projections.reducers import load_events, reduce

psycopg = pytest.importorskip("psycopg")

_PROJECTIONS = ("current_plans, current_work, current_work_dependency, "
                "runtime_work_occupancy, event_links, current_claim_standing, "
                "result_occurrence_quarantine")


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a"),
                            autocommit=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute("TRUNCATE events, event_content, event_plan, event_work, "
                    "event_work_dependency, event_result, event_link, event_observation, "
                    "event_claim, event_work_retired, " + _PROJECTIONS)
    c.commit()
    yield c
    c.close()


def _runtime(conn, node):  # type: ignore[no-untyped-def]
    return Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref=node,
                                                                  actor_ref="pytest"))


def _replay(conn, events):  # type: ignore[no-untyped-def]
    """Truncate projections, reduce `events` in the given order, return a state snapshot."""
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {_PROJECTIONS}")
        for e in events:
            reduce(cur, e)
        cur.execute("SELECT work_ref, execution FROM current_work ORDER BY work_ref")
        work = cur.fetchall()
        cur.execute("SELECT plan_ref, lifecycle, end_reason FROM current_plans ORDER BY plan_ref")
        plans = cur.fetchall()
        cur.execute("SELECT work_ref, dependency_work_ref, dependency_state "
                    "FROM current_work_dependency ORDER BY 1, 2")
        deps = cur.fetchall()
    conn.commit()
    return {"work": work, "plans": plans, "deps": deps}


def test_work_lifecycle_trio_converges_in_all_six_orders(conn) -> None:  # type: ignore[no-untyped-def]
    k = _runtime(conn, "origin-a")
    k.create_plan("p-t", "kawa", "trio")
    k.derive_work("w-t", "p-t", "implement")
    k.record_result("w-t", "success", "r-t")
    k.retire_work("w-t", "obsolete")
    events = load_events(conn)
    trio = [e for e in events if e.kind.value in
            ("work.derived", "result.recorded", "work.retired")]
    fixed = [e for e in events if e not in trio]
    assert len(trio) == 3
    snapshots = {tuple(e.event_id for e in perm): _replay(conn, fixed + list(perm))
                 for perm in permutations(trio)}
    for snap in snapshots.values():
        assert snap["work"] == [("w-t", "retired")]      # retirement dominates, every order


def test_result_before_derive_still_finishes(conn) -> None:  # type: ignore[no-untyped-def]
    k = _runtime(conn, "origin-a")
    k.create_plan("p-f", "kawa", "pair")
    k.derive_work("w-f", "p-f", "implement")
    k.record_result("w-f", "success", "r-f")
    events = load_events(conn)
    pair = [e for e in events if e.kind.value in ("work.derived", "result.recorded")]
    fixed = [e for e in events if e not in pair]
    for perm in permutations(pair):
        snap = _replay(conn, fixed + list(perm))
        assert snap["work"] == [("w-f", "finished")], [e.kind.value for e in perm]


def test_late_old_lifecycle_never_regresses_ended(conn) -> None:  # type: ignore[no-untyped-def]
    """The incremental sibling of the ended→draft inversion: delivering the (earlier-HLC)
    plan.created AFTER the ended lifecycle event must still fold to ended — latest by the
    strict total order wins, not last-write."""
    k = _runtime(conn, "origin-a")
    k.create_plan("p-l", "kawa", "lifecycle")
    k.set_plan_lifecycle("p-l", "ended", end_reason="cancelled")
    events = load_events(conn)
    assert len(events) == 2
    for perm in permutations(events):
        snap = _replay(conn, list(perm))
        assert snap["plans"] == [("p-l", "ended", "cancelled")], [e.kind.value for e in perm]


def test_two_origin_causal_set_converges_in_all_24_orders(conn) -> None:  # type: ignore[no-untyped-def]
    """Parent derive+result (origin-p) x child derive+declare (origin-c): every raw order
    folds to parent finished, edge satisfied, child ready."""
    kp = _runtime(conn, "origin-p")
    kc = _runtime(conn, "origin-c")
    kp.create_plan("p-c", "kawa", "causal")
    kp.derive_work("w-parent", "p-c", "implement")
    kp.record_result("w-parent", "success", "r-parent")
    kc.derive_work("w-child", "p-c", "implement")
    kc.declare_dependency("w-child", "w-parent", "ALL")
    events = load_events(conn)
    quad = [e for e in events if e.kind.value in
            ("work.derived", "result.recorded", "work.dependency_declared")]
    fixed = [e for e in events if e not in quad]
    assert len(quad) == 4
    for perm in permutations(quad):
        snap = _replay(conn, fixed + list(perm))
        assert snap["deps"] == [("w-child", "w-parent", "satisfied")], \
            [e.kind.value for e in perm]
        assert dict(snap["work"]) == {"w-parent": "finished", "w-child": "ready"}, \
            [e.kind.value for e in perm]


def test_real_episode_wb_p2_converges_including_origin_block_order(conn) -> None:  # type: ignore[no-untyped-def]
    """The production episode, structurally reproduced (fixtures-from-real-measurements):
    test-origin residue (plan p2, works w-a/w-b, w-a success) adjudicated from the
    production origin (w-b failure, p2 ended, w-b retired). The origin-block order —
    closures before creations — is the exact 2026-08-17 incident replay."""
    kt = _runtime(conn, "zz-test")           # sorts AFTER the adjudicating origin
    ka = _runtime(conn, "aa-prod")
    kt.create_plan("p2", "kawa", "residue")
    kt.derive_work("w-a", "p2", "implement")
    kt.derive_work("w-b", "p2", "verify")
    kt.record_result("w-a", "success", "r-a")
    ka.record_result("w-b", "failure", "r-b")
    ka.set_plan_lifecycle("p2", "ended", end_reason="cancelled")
    ka.retire_work("w-b", "obsolete")
    events = load_events(conn)
    expected = _replay(conn, events)          # application order = ground truth
    assert dict(expected["work"]) == {"w-a": "finished", "w-b": "retired"}
    assert expected["plans"] == [("p2", "ended", "cancelled")]
    orderings = {
        "reversed": list(reversed(events)),
        "origin_block_prod_first": sorted(events, key=lambda e: (e.origin_node, e.origin_seq)),
        "origin_block_test_first": sorted(events, key=lambda e: (e.origin_node, e.origin_seq),
                                          reverse=True),
    }
    for name, order in orderings.items():
        assert _replay(conn, order) == expected, name
# appended to tests/test_confluence.py by the build step — kept separate for review clarity


def _admit_seq(conn, events):  # type: ignore[no-untyped-def]
    """ADMISSION-order harness (#166 review F3): unlike _replay (which replays over a
    pre-populated fact set), this starts from an EMPTY store and applies each event the
    way admission does — envelope row, then payload row, then reduce — so the fold's
    derive-not-yet-held branches and fact-set lookbacks are exercised for real."""
    from kawa.storage.emit import _insert_payload
    with conn.cursor() as cur:
        cur.execute("TRUNCATE events, event_content, event_plan, event_work, "
                    "event_work_dependency, event_result, event_link, event_observation, "
                    "event_claim, event_work_retired, " + _PROJECTIONS)
        for e in events:
            cur.execute(
                "INSERT INTO events (event_id, origin_node, origin_seq, hlc, kind, "
                "subject_ref, actor_ref, policy_digest, payload_digest, prev_hash, "
                "self_hash, envelope_version, scope_ref, scope_digest, materialized) "
                "VALUES (%s,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,true)",
                (e.event_id, e.origin_node, e.origin_seq, e.hlc, e.kind.value,
                 e.subject_ref, e.actor_ref, e.policy_digest, e.payload_digest,
                 e.prev_hash, e.self_hash, e.envelope_version, e.scope_ref, e.scope_digest),
            )
            _insert_payload(cur, e)
            reduce(cur, e)
        cur.execute("SELECT work_ref, execution FROM current_work ORDER BY work_ref")
        work = cur.fetchall()
        cur.execute("SELECT plan_ref, lifecycle, end_reason FROM current_plans ORDER BY plan_ref")
        plans = cur.fetchall()
        cur.execute("SELECT work_ref, dependency_work_ref, dependency_state "
                    "FROM current_work_dependency ORDER BY 1, 2")
        deps = cur.fetchall()
    conn.commit()
    return {"work": work, "plans": plans, "deps": deps}


def _origin_interleavings(events):  # type: ignore[no-untyped-def]
    """All admission-legal delivery orders: every interleaving of the per-origin streams
    (per-origin order fixed by the chains)."""
    from itertools import permutations as _perms
    streams: dict[str, list] = {}
    for e in events:
        streams.setdefault(e.origin_node, []).append(e)
    for s in streams.values():
        s.sort(key=lambda e: e.origin_seq)
    keys = list(streams)
    tokens = [k for k in keys for _ in streams[k]]
    seen = set()
    for perm in _perms(tokens):
        if perm in seen:
            continue
        seen.add(perm)
        cursors = {k: 0 for k in keys}
        order = []
        for k in perm:
            order.append(streams[k][cursors[k]])
            cursors[k] += 1
        yield perm, order


def test_dependent_with_own_result_survives_parent_cascade(conn) -> None:  # type: ignore[no-untyped-def]
    """Review F1 counterexample, pinned: child finishes, THEN the parent's result fires the
    dependent cascade — the child must stay finished (never revert to ready with a fresh
    ready_at), in both result orders."""
    k = _runtime(conn, "origin-a")
    k.create_plan("p-x", "kawa", "cascade")
    k.derive_work("w-parent", "p-x", "implement")
    k.derive_work("w-child", "p-x", "implement")
    k.declare_dependency("w-child", "w-parent", "ALL")
    k.record_result("w-child", "success", "r-child")
    k.record_result("w-parent", "success", "r-parent")
    events = load_events(conn)
    results = [e for e in events if e.kind.value == "result.recorded"]
    fixed = [e for e in events if e not in results]
    for perm in permutations(results):
        snap = _replay(conn, fixed + list(perm))
        assert dict(snap["work"]) == {"w-parent": "finished", "w-child": "finished"}, \
            [e.event_id[:16] for e in perm]


def test_per_origin_block_admission_converges_with_retired_dependency(conn) -> None:  # type: ignore[no-untyped-def]
    """Review F2 counterexample, pinned at ADMISSION level: three origins, the adjudicating
    origin (result+retire of the parent) delivered FIRST — the declare must read the
    retirement FACT (its projection row does not exist yet) and every admission-legal
    interleaving must converge to edge 'retired', child 'blocked'."""
    kt = _runtime(conn, "zz-test")
    km = _runtime(conn, "cc-mid")
    ka = _runtime(conn, "aa-prod")
    kt.create_plan("p-b", "kawa", "block-order")
    kt.derive_work("w-p", "p-b", "implement")
    km.derive_work("w-c", "p-b", "implement")
    km.declare_dependency("w-c", "w-p", "ALL")
    ka.record_result("w-p", "success", "r-p")
    ka.retire_work("w-p", "obsolete")
    events = load_events(conn)
    expected = _admit_seq(conn, events)       # application order = ground truth
    assert dict(expected["work"]) == {"w-p": "retired", "w-c": "blocked"}
    assert expected["deps"] == [("w-c", "w-p", "retired")]
    checked = 0
    for perm, order in _origin_interleavings(events):
        assert _admit_seq(conn, order) == expected, perm
        checked += 1
    assert checked == 90                      # 6!/(2!·2!·2!) interleavings, all covered
