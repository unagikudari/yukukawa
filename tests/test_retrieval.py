"""Step 3 SQL-first retrieval — the #100 review's eight executable invariants + the
round-2 contract locks, as literal tests (kawa_test_a; #92 isolation)."""
from __future__ import annotations

import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.retrieval import Intent, compile_plan, resolve_bindings, retrieve

psycopg = pytest.importorskip("psycopg")

_ALL = (
    "events, event_links, event_link, event_observation, event_claim, event_plan, "
    "event_work, event_work_dependency, event_result, current_claim_standing, "
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


def _claim_with_mixed_evidence(k, n_sup=3, n_con=3):  # type: ignore[no-untyped-def]
    claim = k.record_claim("service X is healthy")
    for i in range(n_sup):
        o = k.record_observation(f"probe_ok_{i}", value_bool=True, method="http_probe")
        k.assert_link(o.event_id, "supports", claim.event_id)
    for i in range(n_con):
        o = k.record_observation(f"probe_err_{i}", value_bool=False, method="http_probe")
        k.assert_link(o.event_id, "contradicts", claim.event_id)
    return claim


# ---- invariant 1: pure-ref intent plans ZERO lexical classes ----

def test_pure_ref_plans_no_fts(conn, k) -> None:  # type: ignore[no-untyped-def]
    c = k.record_claim("anchored")
    plan = compile_plan(resolve_bindings(conn, Intent(about=c.event_id)))
    assert all(q.backend != "fts" for q in plan.query_classes)
    bundle = retrieve(conn, Intent(about=c.event_id))
    assert bundle.fts_queries == []


# ---- invariant 2: textual intent -> lexical class with fts_reason + tsquery ----

def test_textual_intent_records_fts_provenance(conn, k) -> None:  # type: ignore[no-untyped-def]
    k.record_claim("the replication mesh is stable")
    bundle = retrieve(conn, Intent(text_terms="replication mesh"))
    lex = [q for q in bundle.plan.query_classes if q.backend == "fts"]
    assert len(lex) == 1 and lex[0].fts_reason == "textual"
    assert len(bundle.fts_queries) == 1 and "textual" in bundle.fts_queries[0]
    assert any(r.summary.startswith("the replication mesh") for rs in bundle.sections.values() for r in rs)


# ---- invariant 3 (+ Lock 4): claim anchor expands to >=2 classes, class_id preserved ----

def test_multi_class_expansion_preserves_class_id(conn, k) -> None:  # type: ignore[no-untyped-def]
    claim = _claim_with_mixed_evidence(k)
    bundle = retrieve(conn, Intent(about=claim.event_id))
    purposes = {q.purpose for q in bundle.plan.query_classes}
    assert {"anchor_lookup", "standing", "evidence", "neighborhood"} <= purposes
    assert len(bundle.sections) >= 2
    for class_id, records in bundle.sections.items():
        assert all(r.class_id == class_id for r in records)


# ---- invariant 4: cyclic graph terminates, cycle frontier, stable across runs ----

def test_cycle_terminates_with_frontier_and_stable_output(conn, k) -> None:  # type: ignore[no-untyped-def]
    a = k.record_claim("A"); b = k.record_claim("B")
    k.assert_link(a.event_id, "supports", b.event_id)
    k.assert_link(b.event_id, "supports", a.event_id)
    b1 = retrieve(conn, Intent(about=a.event_id))
    b2 = retrieve(conn, Intent(about=a.event_id))
    assert any(f.reason == "cycle" for f in b1.traversal_frontier)
    assert b1.sections == b2.sections and b1.traversal_frontier == b2.traversal_frontier


# ---- invariant 5 + Lock 2: a biting row cap keeps BOTH sides of the evidence ----

def test_row_cap_frontier_and_fair_share_no_confirmation_bias(conn, k) -> None:  # type: ignore[no-untyped-def]
    claim = _claim_with_mixed_evidence(k, n_sup=6, n_con=6)
    bundle = retrieve(conn, Intent(about=claim.event_id, limit=8, relation_depth=1))
    ev = [s for cid, s in bundle.sections.items() if "evidence" in cid]
    assert ev, "evidence class must produce records"
    kinds = [r.path.split("<-")[1].split("-")[0] for r in ev[0]]
    assert any("supports" in r.path for r in ev[0])
    assert any("contradicts" in r.path for r in ev[0])          # Lock 2: never dropped silently
    nb_frontier = [f for f in bundle.traversal_frontier if f.reason in ("row_cap", "depth_limit")]
    assert nb_frontier                                           # the cut is REPORTED
    b2 = retrieve(conn, Intent(about=claim.event_id, limit=8, relation_depth=1))
    assert bundle.sections == b2.sections                        # deterministic under the cap


# ---- invariant 6: unresolved frontier is separate from traversal frontier ----

def test_unresolved_frontier_separate(conn, k) -> None:  # type: ignore[no-untyped-def]
    """A REAL unresolved link (assert_link takes any target_ref; a ghost target stays
    resolved=false) must surface in unresolved_frontier — and never leak into the
    traversal frontier's reason vocabulary."""
    claim = k.record_claim("claim with a missing basis")
    obs = k.record_observation("p", value_bool=True, method="http_probe")
    k.assert_link(obs.event_id, "supports", claim.event_id)         # resolved edge
    ghost = "sha256:" + "f" * 64
    k.assert_link(claim.event_id, "based_on", ghost)                # target not held -> unresolved

    bundle = retrieve(conn, Intent(about=claim.event_id))
    assert bundle.unresolved_frontier, "the dangling edge must be reported"
    assert {(f.source_node, f.relation, f.next_ref) for f in bundle.unresolved_frontier} == \
        {(claim.event_id, "based_on", ghost)}
    assert all(f.reason == "unresolved" for f in bundle.unresolved_frontier)
    assert all(f.reason in ("cycle", "depth_limit", "row_cap") for f in bundle.traversal_frontier)
    # and the unresolved edge contributed NOTHING to sections (unresolved derives nothing)
    assert all(r.ref != ghost for rs in bundle.sections.values() for r in rs)


# ---- fallback_policy: deferred lexical fires only on structural underflow ----

def test_fallback_policy_skips_lexical_when_structure_suffices(conn, k) -> None:  # type: ignore[no-untyped-def]
    claim = _claim_with_mixed_evidence(k, 2, 2)
    bundle = retrieve(conn, Intent(about=claim.event_id, text_terms="healthy",
                                   fallback_policy=1))              # structure will exceed 1
    assert bundle.fts_queries == []                                 # lexical did NOT fire
    assert bundle.skipped_classes and "threshold met" in bundle.skipped_classes[0]


def test_fallback_policy_fires_lexical_on_underflow(conn, k) -> None:  # type: ignore[no-untyped-def]
    k.record_claim("the replication mesh is stable")
    bundle = retrieve(conn, Intent(text_terms="replication mesh",
                                   fallback_policy=5))              # no anchor: structural total 0 < 5
    lex = [q for q in bundle.plan.query_classes if q.backend == "fts"]
    assert lex and lex[0].fts_reason == "structured_underflow"
    assert len(bundle.fts_queries) == 1 and "structured_underflow" in bundle.fts_queries[0]


# ---- invariant 7: path budget truncates deterministically ----

def test_path_truncation_deterministic(conn, k) -> None:  # type: ignore[no-untyped-def]
    prev = k.record_claim("c0")
    first = prev
    for i in range(1, 12):
        nxt = k.record_claim(f"c{i}")
        k.assert_link(prev.event_id, "based_on", nxt.event_id)
        prev = nxt
    b1 = retrieve(conn, Intent(about=first.event_id, relation_depth=11, limit=100))
    b2 = retrieve(conn, Intent(about=first.event_id, relation_depth=11, limit=100))
    deep = [r for rs in b1.sections.values() for r in rs if r.path_truncated]
    assert deep, "beyond the edge budget, paths must truncate"
    assert b1.sections == b2.sections


# ---- invariant 8: the harness surfaces provenance + frontiers without MCP ----

def test_ask_harness_output(conn, k, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    import importlib
    claim = _claim_with_mixed_evidence(k, n_sup=1, n_con=1)
    import sys
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / "scripts"))
    ask = importlib.import_module("ask")
    monkeypatch.setattr("kawa.storage.db.connect", lambda dsn=None: conn)
    monkeypatch.setattr(ask, "connect", lambda dsn=None: conn)
    monkeypatch.setattr("sys.argv", ["ask.py", "--about", claim.event_id])
    conn.close = lambda: None  # type: ignore[method-assign]      # harness closes; fixture reuses
    ask.main()
    outp = capsys.readouterr().out
    assert "plan:" in outp and "standing" in outp
    assert "q0-anchor_lookup" in outp


# ---- carried invariants: standing verbatim; empty classes stated ----

def test_standing_attached_verbatim_and_empty_classes_stated(conn, k) -> None:  # type: ignore[no-untyped-def]
    c = k.record_claim("lonely claim")                             # no links at all
    bundle = retrieve(conn, Intent(about=c.event_id))
    anchor = bundle.sections["q0-anchor_lookup"][0]
    assert anchor.standing == "unevaluated"                        # verbatim from projection
    assert any("evidence" in e for e in bundle.empty_classes)      # ran empty, said so
    assert any("neighborhood" in e for e in bundle.empty_classes)


def test_console_search_screen_renders_bundle(conn, k) -> None:  # type: ignore[no-untyped-def]
    from kawa.console.render import render
    claim = _claim_with_mixed_evidence(k, 1, 1)
    empty = render(conn, "/search")
    assert empty is not None and "retrieve" in empty               # bare form, no query ran
    page = render(conn, "/search", {"about": [claim.event_id]})
    assert page is not None and "Retrieval plan" in page and "q0-anchor_lookup" in page


# ---- Lock 3: budget apportionment is deterministic and sums to >= limit split ----

def test_budget_apportionment_deterministic(conn, k) -> None:  # type: ignore[no-untyped-def]
    c = k.record_claim("x")
    plan = compile_plan(resolve_bindings(conn, Intent(about=c.event_id, limit=10)))
    budgets = [q.budget for q in plan.query_classes]
    assert sum(budgets) >= 10 and max(budgets) - min(budgets) <= 1   # floor split + remainder
    plan2 = compile_plan(resolve_bindings(conn, Intent(about=c.event_id, limit=10)))
    assert plan == plan2
