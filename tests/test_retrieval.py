"""Step 3 SQL-first retrieval — the #100 review's eight executable invariants + the
round-2 contract locks, as literal tests (kawa_test_a; #92 isolation)."""
from __future__ import annotations

import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.retrieval import FLEET_SCOPES as FLEET
from kawa.retrieval import Intent, compile_plan, resolve_bindings, retrieve

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
    plan = compile_plan(resolve_bindings(conn, Intent(about=c.event_id), viewer_scopes=FLEET))
    assert all(q.backend != "fts" for q in plan.query_classes)
    bundle = retrieve(conn, Intent(about=c.event_id), viewer_scopes=FLEET)
    assert bundle.fts_queries == []


# ---- invariant 2: textual intent -> lexical class with fts_reason + tsquery ----

def test_textual_intent_records_fts_provenance(conn, k) -> None:  # type: ignore[no-untyped-def]
    k.record_claim("the replication mesh is stable")
    bundle = retrieve(conn, Intent(text_terms="replication mesh"), viewer_scopes=FLEET)
    lex = [q for q in bundle.plan.query_classes if q.backend == "fts"]
    assert len(lex) == 1 and lex[0].fts_reason == "textual"
    assert len(bundle.fts_queries) == 1 and "textual" in bundle.fts_queries[0]
    assert any(r.summary.startswith("the replication mesh") for rs in bundle.sections.values() for r in rs)


# ---- invariant 3 (+ Lock 4): claim anchor expands to >=2 classes, class_id preserved ----

def test_multi_class_expansion_preserves_class_id(conn, k) -> None:  # type: ignore[no-untyped-def]
    claim = _claim_with_mixed_evidence(k)
    bundle = retrieve(conn, Intent(about=claim.event_id), viewer_scopes=FLEET)
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
    b1 = retrieve(conn, Intent(about=a.event_id), viewer_scopes=FLEET)
    b2 = retrieve(conn, Intent(about=a.event_id), viewer_scopes=FLEET)
    assert any(f.reason == "cycle" for f in b1.traversal_frontier)
    assert b1.sections == b2.sections and b1.traversal_frontier == b2.traversal_frontier


# ---- invariant 5 + Lock 2: a biting row cap keeps BOTH sides of the evidence ----

def test_row_cap_frontier_and_fair_share_no_confirmation_bias(conn, k) -> None:  # type: ignore[no-untyped-def]
    claim = _claim_with_mixed_evidence(k, n_sup=6, n_con=6)
    bundle = retrieve(conn, Intent(about=claim.event_id, limit=8, relation_depth=1), viewer_scopes=FLEET)
    ev = [s for cid, s in bundle.sections.items() if "evidence" in cid]
    assert ev, "evidence class must produce records"
    kinds = [r.path.split("<-")[1].split("-")[0] for r in ev[0]]
    assert any("supports" in r.path for r in ev[0])
    assert any("contradicts" in r.path for r in ev[0])          # Lock 2: never dropped silently
    nb_frontier = [f for f in bundle.traversal_frontier if f.reason in ("row_cap", "depth_limit")]
    assert nb_frontier                                           # the cut is REPORTED
    b2 = retrieve(conn, Intent(about=claim.event_id, limit=8, relation_depth=1), viewer_scopes=FLEET)
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

    bundle = retrieve(conn, Intent(about=claim.event_id), viewer_scopes=FLEET)
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
                                   fallback_policy=1), viewer_scopes=FLEET)              # structure will exceed 1
    assert bundle.fts_queries == []                                 # lexical did NOT fire
    skipped = [s for s in bundle.skipped_classes if s.reason == "structured_underflow_met"]
    assert skipped and skipped[0].purpose == "lexical"       # typed, not prose (#214 step 2)


def test_fallback_policy_fires_lexical_on_underflow(conn, k) -> None:  # type: ignore[no-untyped-def]
    k.record_claim("the replication mesh is stable")
    bundle = retrieve(conn, Intent(text_terms="replication mesh",
                                   fallback_policy=5), viewer_scopes=FLEET)              # no anchor: structural total 0 < 5
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
    b1 = retrieve(conn, Intent(about=first.event_id, relation_depth=11, limit=100), viewer_scopes=FLEET)
    b2 = retrieve(conn, Intent(about=first.event_id, relation_depth=11, limit=100), viewer_scopes=FLEET)
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
    real_close = conn.close
    conn.close = lambda: None  # type: ignore[method-assign]      # harness closes; fixture reuses
    try:
        ask.main()
    finally:
        conn.close = real_close  # type: ignore[method-assign]
        conn.rollback()          # end the read txn NOW — a lingering 'idle in transaction'
                                 # holds ACCESS SHARE locks and deadlocks the next test's
                                 # TRUNCATE if GC is late closing the leaked handle
    outp = capsys.readouterr().out
    assert "plan:" in outp and "standing" in outp
    assert "q0-anchor_lookup" in outp


# ---- carried invariants: standing verbatim; empty classes stated ----

def test_standing_attached_verbatim_and_empty_classes_stated(conn, k) -> None:  # type: ignore[no-untyped-def]
    c = k.record_claim("lonely claim")                             # no links at all
    bundle = retrieve(conn, Intent(about=c.event_id), viewer_scopes=FLEET)
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


# ---- Lock 3: apportionment is deterministic, and `limit` is a CEILING (#214 step 2) ----

def test_budget_apportionment_deterministic(conn, k) -> None:  # type: ignore[no-untyped-def]
    """This assertion used to read `sum(budgets) >= 10`, which encoded `limit` as a
    minimum the planner could enlarge — the defect #211 reported. It is now `<=`,
    over EVERY class including the sideband, because the whole point is that no
    class lives outside the number the caller gave."""
    c = k.record_claim("x")
    plan = compile_plan(resolve_bindings(conn, Intent(about=c.event_id, limit=10), viewer_scopes=FLEET))
    assert sum(q.budget for q in plan.query_classes) <= 10
    plan2 = compile_plan(resolve_bindings(conn, Intent(about=c.event_id, limit=10), viewer_scopes=FLEET))
    assert plan == plan2                                            # still deterministic


# ---- #146 (ADV-02): scope boundaries enforced across all retrieval backends ----

@pytest.fixture()
def k_restricted(conn):  # type: ignore[no-untyped-def]
    """A second emitter writing into a scope the default viewer is NOT granted."""
    return Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"),
                default_scope="proj-restricted")


def test_scope_restricted_anchor_does_not_bind(conn, k, k_restricted) -> None:  # type: ignore[no-untyped-def]
    """An out-of-scope ref binds to nothing under default grants (not_found — existence is
    not disclosed), and binds normally once the scope is granted."""
    secret = k_restricted.record_claim("restricted proposition")
    bound = resolve_bindings(conn, Intent(about=secret.event_id), viewer_scopes=FLEET)
    assert bound.anchor_kind is None and bound.anchor_ref is None
    bundle = retrieve(conn, Intent(about=secret.event_id), viewer_scopes=FLEET)
    assert bundle.sections == {}                                   # nothing disclosed
    granted = retrieve(conn, Intent(about=secret.event_id),
                       viewer_scopes=frozenset({"fleet", "proj-restricted"}))
    assert any(r.summary == "restricted proposition"
               for rs in granted.sections.values() for r in rs)


def test_scope_lexical_withholds_restricted_claims(conn, k, k_restricted) -> None:  # type: ignore[no-untyped-def]
    """Restricted claims never surface through FTS — and the lexical class reports NO
    withheld count either (F2: caller-chosen terms + exact counts = a blind existence
    oracle over restricted propositions)."""
    k.record_claim("alpha finding shared")
    k_restricted.record_claim("alpha finding restricted")
    bundle = retrieve(conn, Intent(text_terms="alpha finding"), viewer_scopes=FLEET)
    summaries = [r.summary for rs in bundle.sections.values() for r in rs]
    assert "alpha finding shared" in summaries
    assert "alpha finding restricted" not in summaries
    assert bundle.scope_withheld == {}                             # no oracle: not even a count
    assert bundle.viewer_scopes == ("fleet",)
    granted = retrieve(conn, Intent(text_terms="alpha finding"),
                       viewer_scopes=frozenset({"fleet", "proj-restricted"}))
    gsum = [r.summary for rs in granted.sections.values() for r in rs]
    assert "alpha finding restricted" in gsum


def test_scope_mixed_plan_shows_only_in_scope_revision(conn, k, k_restricted) -> None:  # type: ignore[no-untyped-def]
    """F1: a plan visible through an in-scope event must not leak a restricted later
    revision's objective — neither via the anchor lookup nor via plan FTS (both read
    the latest IN-SCOPE objective, never the unscoped projection)."""
    k.create_plan("plan-mixed", "proj", "public objective alpha")
    k_restricted.create_plan("plan-mixed", "proj", "secret objective omega")   # later revision
    bundle = retrieve(conn, Intent(about="plan-mixed"), viewer_scopes=FLEET)
    anchor = bundle.sections["q0-anchor_lookup"][0]
    assert "public objective alpha" in anchor.summary
    assert "omega" not in anchor.summary
    fts = retrieve(conn, Intent(text_terms="objective alpha"), viewer_scopes=FLEET)
    fsum = [r.summary for rs in fts.sections.values() for r in rs]
    assert any("public objective alpha" in s for s in fsum)
    fts_secret = retrieve(conn, Intent(text_terms="secret objective omega"), viewer_scopes=FLEET)
    assert all("omega" not in r.summary
               for rs in fts_secret.sections.values() for r in rs)


def test_scope_restricted_link_assertion_between_public_events_withheld(conn, k, k_restricted) -> None:  # type: ignore[no-untyped-def]
    """F4: an edge asserted in a restricted scope between two PUBLIC events is itself
    restricted content — not expanded, not in frontiers, counted only."""
    a = k.record_claim("public claim A")
    b = k.record_claim("public claim B")
    k_restricted.assert_link(a.event_id, "supports", b.event_id)
    bundle = retrieve(conn, Intent(about=b.event_id), viewer_scopes=FLEET)
    refs = {r.ref for rs in bundle.sections.values() for r in rs}
    assert a.event_id not in refs                                  # edge not traversed
    assert sum(bundle.scope_withheld.values()) >= 1
    granted = retrieve(conn, Intent(about=b.event_id),
                       viewer_scopes=frozenset({"fleet", "proj-restricted"}))
    grefs = {r.ref for rs in granted.sections.values() for r in rs}
    assert a.event_id in grefs


def test_scope_evidence_and_neighborhood_withhold_without_leaking_refs(conn, k, k_restricted) -> None:  # type: ignore[no-untyped-def]
    """Restricted evidence neither appears in sections nor leaks its ref/relation through
    any frontier — only an aggregate count remains."""
    claim = k.record_claim("service X is healthy")
    ok = k.record_observation("probe_ok", value_bool=True, method="http_probe")
    k.assert_link(ok.event_id, "supports", claim.event_id)
    secret_obs = k_restricted.record_observation("secret_probe", value_bool=False, method="http_probe")
    k.assert_link(secret_obs.event_id, "contradicts", claim.event_id)

    bundle = retrieve(conn, Intent(about=claim.event_id), viewer_scopes=FLEET)
    refs = {r.ref for rs in bundle.sections.values() for r in rs}
    assert ok.event_id in refs
    assert secret_obs.event_id not in refs
    frontier_refs = {f.next_ref for f in bundle.traversal_frontier} | \
        {f.next_ref for f in bundle.unresolved_frontier} | \
        {f.source_node for f in bundle.traversal_frontier} | \
        {f.source_node for f in bundle.unresolved_frontier}
    assert secret_obs.event_id not in frontier_refs                # no ref leak, ever
    assert sum(bundle.scope_withheld.values()) >= 2                # evidence + neighborhood counts
    # grant the scope -> the contradicting evidence appears (Lock 2 still fair)
    granted = retrieve(conn, Intent(about=claim.event_id),
                       viewer_scopes=frozenset({"fleet", "proj-restricted"}))
    grefs = {r.ref for rs in granted.sections.values() for r in rs}
    assert secret_obs.event_id in grefs
    assert not granted.scope_withheld


def test_scope_unscoped_legacy_events_remain_visible(conn) -> None:  # type: ignore[no-untyped-def]
    """Envelope-v1 events (scope_ref IS NULL, the sql/0010 rolling-transition carve-out)
    stay visible under any grant set — legacy data predates scope commitments."""
    legacy = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"),
                  default_scope=None)
    c = legacy.record_claim("legacy unscoped claim")
    bundle = retrieve(conn, Intent(about=c.event_id), viewer_scopes=FLEET)
    assert any(r.ref == c.event_id for rs in bundle.sections.values() for r in rs)
    assert not bundle.scope_withheld


def test_scope_filter_is_deterministic(conn, k, k_restricted) -> None:  # type: ignore[no-untyped-def]
    claim = k.record_claim("service X is healthy")
    for i in range(3):
        o = k_restricted.record_observation(f"sec_{i}", value_bool=True, method="http_probe")
        k.assert_link(o.event_id, "supports", claim.event_id)
    b1 = retrieve(conn, Intent(about=claim.event_id), viewer_scopes=FLEET)
    b2 = retrieve(conn, Intent(about=claim.event_id), viewer_scopes=FLEET)
    assert b1.sections == b2.sections and b1.scope_withheld == b2.scope_withheld


# ---- #214 step 3: the ceiling holds over RECORDS, not just over budgets ----

@pytest.mark.parametrize("limit", [0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 16, 59])
def test_records_returned_never_exceed_the_limit(conn, k, limit) -> None:  # type: ignore[no-untyped-def]
    """Step 2 made `sum(class budgets) <= limit` true at compile time. Its review
    then demonstrated the runtime gap: `_exec_evidence` computed
    `per = max(budget // 2, 1)` and applied it to BOTH relations, so a class with
    budget 1 returned two records and the Bundle exceeded the ceiling by one.

    This asserts the property callers actually care about — how many records come
    back — with an anchor that has more evidence on both sides than any budget
    here can hold, so the cap genuinely bites.

    Note what it does NOT reliably catch, measured: run against the pre-step-3
    executor this test still PASSES, because slack in other classes (which return
    fewer rows than their budget) absorbs the overrun. The class-level assertion
    below is the one that bites — it fails there with
    `limit=4: evidence returned 2 > 1`. This one guards the caller-facing
    property; that one guards the mechanism."""
    claim = _claim_with_mixed_evidence(k, n_sup=6, n_con=6)
    bundle = retrieve(conn, Intent(about=claim.event_id, limit=limit), viewer_scopes=FLEET)
    total = sum(len(records) for records in bundle.sections.values())
    assert total <= limit, f"limit={limit} returned {total} records"


def test_evidence_never_exceeds_its_own_class_budget(conn, k) -> None:  # type: ignore[no-untyped-def]
    """The class-level half of the same property, for every budget the planner can
    hand it — `#211` measured the overrun at every odd budget below 4."""
    claim = _claim_with_mixed_evidence(k, n_sup=6, n_con=6)
    for limit in range(0, 20):
        bundle = retrieve(conn, Intent(about=claim.event_id, limit=limit), viewer_scopes=FLEET)
        for qc in bundle.plan.query_classes:
            if qc.purpose == "evidence":
                got = len(bundle.sections.get(qc.class_id, []))
                assert got <= qc.budget, f"limit={limit}: evidence returned {got} > {qc.budget}"


def test_a_biting_cap_keeps_both_sides_or_plans_neither(conn, k) -> None:  # type: ignore[no-untyped-def]
    """Lock 2 and the ceiling used to be in direct conflict at budget 1: one row
    cannot represent two sides, and the executor resolved it by returning two —
    breaking the bound to keep the fairness.

    Resolved at the floor instead. Evidence is planned with at least 2 or not
    planned at all, so whenever it runs, both sides are representable; and a
    ceiling that cannot afford balanced evidence says so rather than quietly
    returning one side for a reader to mistake for the whole."""
    claim = _claim_with_mixed_evidence(k, n_sup=6, n_con=6)
    for limit in range(0, 20):
        bundle = retrieve(conn, Intent(about=claim.event_id, limit=limit), viewer_scopes=FLEET)
        ev = [q for q in bundle.plan.query_classes if q.purpose == "evidence"]
        if not ev:
            skipped = {s.purpose for s in bundle.skipped_classes}
            assert "evidence" in skipped, f"limit={limit}: evidence vanished without a word"
            continue
        assert ev[0].budget >= 2, f"limit={limit}: evidence planned below its floor"
        records = bundle.sections.get(ev[0].class_id, [])
        if len(records) >= 2:
            paths = " ".join(r.path for r in records)
            assert "supports" in paths and "contradicts" in paths, f"limit={limit}: one-sided"
