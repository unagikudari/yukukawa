"""#141 — current_relations projection + bounded traversal (plan-relations-projection rev 3).

Logic tests on the fenced test DB; real-log benchmark/parity live in
scripts/relations_measure.py as signed Observations (the #122 measure_* pattern — pytest
is fenced off the dogfood DB by 12A)."""
from __future__ import annotations

import os

import psycopg
import pytest

from kawa import relations as rel
from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute("TRUNCATE content_embedding, event_content, events, event_links, "
                    "event_link, event_observation, event_claim, event_plan, event_work, "
                    "event_work_dependency, event_work_retired, event_result, "
                    "current_claim_standing, current_plans, current_work, "
                    "current_work_dependency, runtime_work_occupancy, work_dispatch")
    c.commit()
    yield c
    c.close()


def _k(conn):  # type: ignore[no-untyped-def]
    return Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="rtest",
                                                                  actor_ref="pytest"))


def test_relation_registry_is_exact() -> None:
    """The frozen Event→Relation mapping (rev 3 §2) — a relation added without a registry
    decision fails HERE; unevidenced relations (governs/reviews/finds/supersedes) absent."""
    assert rel.RELATION_REGISTRY == {
        "derives":    ("work.derived",             "plan",   "work"),
        "depends_on": ("work.dependency_declared", "work",   "work"),
        "evidences":  ("result.recorded",          "result", "work"),
        "based_on":   ("link.asserted",            "*",      "*"),
    }
    assert rel.STANDING == ("current", "superseded", "retired", "unknown")
    assert "unknown" in rel.SUBJECT_KINDS


def test_evidenced_edges_appear_with_pinned_directions(conn) -> None:  # type: ignore[no-untyped-def]
    k = _k(conn)
    k.create_plan("rp", "kawa", "relations fixture")
    k.derive_work("rw1", "rp", "implement")
    k.derive_work("rw2", "rp", "implement")
    k.declare_dependency("rw2", "rw1", "ALL")
    k.record_result("rw1", "success", "rr1")
    with conn.cursor() as cur:
        cur.execute("SELECT source_kind, source_id, relation_kind, target_kind, target_id "
                    "FROM current_relations ORDER BY relation_kind, target_id")
        rows = set(cur.fetchall())
    assert ("plan", "rp", "derives", "work", "rw1") in rows
    assert ("work", "rw2", "depends_on", "work", "rw1") in rows      # dependent -> dependency
    assert ("result", "rr1", "evidences", "work", "rw1") in rows     # evidence points AT the claim


def test_latest_assertion_per_tuple_and_audit_stays_in_log(conn) -> None:  # type: ignore[no-untyped-def]
    """Re-derive of the same (plan, work) pair: the projection exposes the LATEST basis;
    the prior assertion remains in the append-only log (rev 3 §3)."""
    k = _k(conn)
    k.create_plan("rp2", "kawa", "re-derive fixture")
    e1 = k.derive_work("rwx", "rp2", "implement")
    e2 = k.derive_work("rwx", "rp2", "review")           # re-derive, same pair
    with conn.cursor() as cur:
        cur.execute("SELECT basis_event FROM current_relations "
                    "WHERE relation_kind='derives' AND source_id='rp2' AND target_id='rwx'")
        rows = cur.fetchall()
        assert len(rows) == 1 and rows[0][0] == e2.event_id
        cur.execute("SELECT count(*) FROM event_work WHERE work_ref='rwx'")
        assert cur.fetchone()[0] == 2                    # audit surface intact
    assert e1.event_id != e2.event_id


def test_based_on_kind_resolution_and_unknown_is_a_leaf(conn) -> None:  # type: ignore[no-untyped-def]
    k = _k(conn)
    p = k.create_plan("rp3", "kawa", "link fixture")
    o = k.record_observation("probe", value_bool=True, method="metric_read")
    k.assert_link(p.event_id, "based_on", o.event_id)
    k.assert_link(p.event_id, "based_on", "sha256:" + "f" * 64)      # unresolvable ref
    with conn.cursor() as cur:
        cur.execute("SELECT source_kind, target_kind, target_id FROM current_relations "
                    "WHERE relation_kind='based_on' ORDER BY target_id")
        rows = cur.fetchall()
    kinds = {(r[0], r[1]) for r in rows}
    assert ("plan", "observation") in kinds              # resolved via event kinds
    assert any(r[1] == "unknown" for r in rows)          # unresolvable ref stored, typed unknown
    ghost = next(r for r in rows if r[1] == "unknown")
    walk = rel.traverse(conn, "unknown", ghost[2], depth=3)
    assert walk["edges"] == [] or all(
        e["relation_kind"] == "based_on" for e in walk["edges"])     # unknown expands nothing new


def test_standing_joined_at_read_closed_vocabulary(conn) -> None:  # type: ignore[no-untyped-def]
    k = _k(conn)
    k.create_plan("rp4", "kawa", "standing fixture")
    k.set_plan_lifecycle("rp4", "running")
    k.derive_work("rws", "rp4", "implement")
    k.retire_work("rws", "superseded")
    with conn.cursor() as cur:
        assert rel.standing_of(cur, "plan", "rp4") == "current"
        assert rel.standing_of(cur, "work", "rws") == "retired"
        assert rel.standing_of(cur, "plan", "no-such-plan") == "unknown"   # missing row: loud
        assert rel.standing_of(cur, "result", "anything") == "current"     # immutable event
        assert rel.standing_of(cur, "unknown", "x") == "unknown"
    k.set_plan_lifecycle("rp4", "ended", end_reason="superseded")
    with conn.cursor() as cur:
        assert rel.standing_of(cur, "plan", "rp4") == "superseded"


def test_traversal_bidirectional_and_caps_typed(conn) -> None:  # type: ignore[no-untyped-def]
    k = _k(conn)
    k.create_plan("rp5", "kawa", "caps fixture")
    for i in range(rel.MAX_FAN_OUT + 5):                 # exceed per-node fan-out
        k.derive_work(f"rwf{i:02d}", "rp5", "implement")
    from_plan = rel.traverse(conn, "plan", "rp5", depth=1)
    assert from_plan["is_truncated"] is True and from_plan["truncated_at_depth"] == 1
    assert from_plan["total_edges_discovered"] == len(from_plan["edges"]) == rel.MAX_FAN_OUT
    from_work = rel.traverse(conn, "work", "rwf00", depth=1)         # reverse direction
    assert any(e["source_id"] == "rp5" and e["relation_kind"] == "derives"
               for e in from_work["edges"])
    assert set(from_plan) >= {"is_truncated", "truncated_at_depth", "total_edges_discovered",
                              "node_standing"}           # typed truncation schema (rev 3 §5)


def test_traversal_depth_capped_and_deterministic(conn) -> None:  # type: ignore[no-untyped-def]
    k = _k(conn)
    k.create_plan("rp6", "kawa", "depth fixture")
    k.derive_work("rda", "rp6", "implement")
    k.derive_work("rdb", "rp6", "implement")
    k.declare_dependency("rdb", "rda", "ALL")
    k.record_result("rda", "success", "rres")
    one = rel.traverse(conn, "plan", "rp6", depth=99)    # clamped to MAX_DEPTH
    assert one["depth"] == rel.MAX_DEPTH
    again = rel.traverse(conn, "plan", "rp6", depth=99)
    assert one == again                                  # deterministic across invocations
