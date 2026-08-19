"""Neighborhood expansion issues a bounded number of statements (#212, #214 step 4).

The defect: `_exec_neighborhood` ranked edges with a sort key that opened a cursor
and ran `SELECT hlc, origin_node FROM events WHERE event_id = %s` per edge. Python's
`list.sort` calls the key once per element (decorate-sort-undecorate), so that was
exactly one round trip per edge — issued *before* the row cap was consulted. A node
with 100k visible edges paid 100k round trips to return five rows.

Counted rather than timed: a slow query is noise, a statement count is the mechanism.
"""
from __future__ import annotations

import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.retrieval import FLEET_SCOPES as FLEET
from kawa.retrieval import (
    CANDIDATE_PER_NODE,
    Intent,
    candidate_edges,
    retrieve,
    scope_pairs,
)

psycopg = pytest.importorskip("psycopg")

_TABLES = ("content_embedding, event_content, events, event_links, event_link, "
           "event_observation, event_claim, event_plan, event_work, "
           "event_work_dependency, event_work_retired, event_result, "
           "current_claim_standing, current_plans, current_work, "
           "current_work_dependency, runtime_work_occupancy, work_dispatch, "
           "situation_rollup, fleet_node, evidence_provenance, projection_state, "
           "fleet_node_facet")


class CountingConnection:
    """Wraps a connection so every statement its cursors run is counted.

    Deliberately a wrapper rather than a Postgres-side counter: it counts what the
    APPLICATION issued, which is the round-trip cost the defect was about."""

    def __init__(self, conn):  # type: ignore[no-untyped-def]
        self._conn, self.statements = conn, []

    def cursor(self, *a, **kw):  # type: ignore[no-untyped-def]
        return _CountingCursor(self._conn.cursor(*a, **kw), self.statements)

    def execute(self, query, params=None, **kw):  # type: ignore[no-untyped-def]
        """psycopg3 lets callers run a statement straight off the connection.
        Without this the counter would silently miss anything issued that way —
        a measuring mechanism with a hole in it, which is the failure this file
        exists to catch in someone else's code (review round 1)."""
        cur = self.cursor()
        return cur.execute(query, params, **kw) if params is not None else \
            cur.execute(query, **kw)

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        return getattr(self._conn, name)


class _CountingCursor:
    def __init__(self, cur, log):  # type: ignore[no-untyped-def]
        self._cur, self._log = cur, log

    def execute(self, query, params=None, **kw):  # type: ignore[no-untyped-def]
        self._log.append(str(query).split()[0].upper() if str(query).strip() else "")
        return self._cur.execute(query, params, **kw) if params is not None else \
            self._cur.execute(query, **kw)

    def __enter__(self):  # type: ignore[no-untyped-def]
        self._cur.__enter__()
        return self

    def __exit__(self, *a):  # type: ignore[no-untyped-def]
        return self._cur.__exit__(*a)

    def __getattr__(self, name):  # type: ignore[no-untyped-def]
        return getattr(self._cur, name)


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a"),
                            autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_TABLES}")
    c.commit()
    yield c
    c.close()


def _hub(conn, degree):  # type: ignore[no-untyped-def]
    """An anchor with `degree` visible edges — the high-degree shape #212 describes."""
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest"))
    claim = k.record_claim("the hub")
    for i in range(degree):
        o = k.record_observation(f"probe_{i}", value_bool=True, method="http_probe")
        k.assert_link(o.event_id, "supports", claim.event_id)
    conn.commit()
    return claim


def _statements_for(conn, anchor, limit=12):  # type: ignore[no-untyped-def]
    """`limit` must be high enough that the neighborhood class is actually PLANNED.
    Step 2's tiers put it in tier 3, so at limit=5 a claim anchor spends the whole
    ceiling on tiers 1 and 2 and the executor under test never runs — the first
    cut of this test measured that and passed against the defect."""
    counting = CountingConnection(conn)
    retrieve(counting, Intent(about=anchor.event_id, limit=limit), viewer_scopes=FLEET)
    return len(counting.statements)


def test_statement_count_does_not_grow_with_degree(conn) -> None:  # type: ignore[no-untyped-def]
    """The property, stated as a comparison rather than an absolute: whatever the
    fixed overhead is, quadrupling the edges must not multiply the statements.

    Against the previous code this fails outright — the per-edge lookup made the
    count grow one-for-one with the edges."""
    small = _statements_for(conn, _hub(conn, 10))

    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {_TABLES}")
    conn.commit()
    large = _statements_for(conn, _hub(conn, 40))

    # Measured, both ways round: before the fix 36 -> 66 (one statement per extra
    # edge); after, 25 -> 25. The threshold is generous enough to survive an extra
    # fixed query being added later and tight enough that per-edge growth cannot hide.
    assert large <= small + 5, (
        f"statements grew from {small} to {large} as edges went 10 -> 40; "
        "the per-edge lookup is back")


def test_a_bounded_request_over_a_hub_stays_bounded_in_records(conn) -> None:  # type: ignore[no-untyped-def]
    """The caller-facing half: a hub must not overflow the ceiling either."""
    anchor = _hub(conn, 40)
    bundle = retrieve(conn, Intent(about=anchor.event_id, limit=12), viewer_scopes=FLEET)
    total = sum(len(rs) for rs in bundle.sections.values())
    assert total <= 12, total


def test_the_cut_is_reported(conn) -> None:  # type: ignore[no-untyped-def]
    """A hub that exceeds the row cap says so, rather than returning a quiet
    prefix a reader would mistake for the whole neighbourhood."""
    anchor = _hub(conn, 40)
    bundle = retrieve(conn, Intent(about=anchor.event_id, limit=12), viewer_scopes=FLEET)
    assert any(f.reason in ("row_cap", "depth_limit") for f in bundle.traversal_frontier)


def test_both_scope_columns_are_not_null(conn) -> None:  # type: ignore[no-untyped-def]
    """A precondition of the scoped leaves, pinned because it is load-bearing in a
    way that is easy to relax by accident (#222 round 2).

    In SQL `NULL = NULL` and `NULL <> NULL` are BOTH unknown. A leaf naming the
    scope pair therefore cannot match a row with a NULL in either column, and the
    row would become invisible to everyone rather than to the wrong people — a
    silent, total disappearance.

    0026 made both NOT NULL so a stored sentinel keeps the predicate seekable. The
    stronger reason is this one, and it was not written down until the review found
    it.

    sql/0030 splits the endpoint column per direction and makes exactly ONE of the
    three nullable. That is not a relaxation of this rule, it is the rule used
    deliberately: `source_scope_ref` is NULL precisely when this node does not hold
    the source event, and "invisible to everyone" is then the correct answer -- you
    cannot traverse to an event you do not have. The pinning is therefore per column
    rather than blanket, so a future column that becomes nullable by ACCIDENT still
    fails here."""
    with conn.cursor() as cur:
        cur.execute("SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name='event_links' AND column_name IN "
                    "('target_scope_ref','asserter_scope_ref','source_scope_ref')")
        nullable = {name: flag for name, flag in cur.fetchall()}
    assert nullable == {"target_scope_ref": "NO", "asserter_scope_ref": "NO",
                        "source_scope_ref": "YES"}, nullable


def test_an_unheld_source_is_unreachable_rather_than_mis_scoped(conn) -> None:  # type: ignore[no-untyped-def]
    """The behaviour the nullable column buys, asserted rather than assumed.

    Before sql/0030 the in-direction leaf authorised the step by the scope of the
    node it was ALREADY standing on, because the single denormalised column carried
    the TARGET's scope in both directions. A restricted event pointing at a public
    one was reachable by anyone who could see the public one."""
    from kawa.retrieval import candidate_edges, scope_pairs

    with conn.cursor() as cur:
        cur.execute("INSERT INTO event_links(source_ref, relation, target_ref, resolved,"
                    " asserted_by_event_id, target_scope_ref, asserter_scope_ref)"
                    " VALUES ('ghost-src','supports','pub-node',true,'a1','$public','$public')")
        pairs, _ = scope_pairs(frozenset({"$public"}))
        rows, _ = candidate_edges(cur, "pub-node", pairs, 20)
    assert rows == [], rows            # the in-leaf cannot step to an unheld source


# The security property is asserted on the CURRENT index shape, in
# test_one_leaf_per_pair_covers_every_relation_in_one_seek below. An earlier version
# of this test asserted it against `event_links_out_scoped_idx`, which sql/0028
# replaced with the ranked index; keeping both would have pinned an index that no
# longer exists while looking like extra coverage.


# ---- the leaf planner: one seek per scope pair, ordered by the index (#222 rev 6) ----

def test_pairs_are_deterministic_and_same_scope_first(conn) -> None:  # type: ignore[no-untyped-def]
    """Identical requests must consult identical pairs, or the truncation itself
    becomes a source of irreproducibility. Same-scope pairs lead because they are
    the overwhelming majority of real edges, so a budget that binds spends itself
    on them before cross-scope combinations."""
    from kawa.retrieval import scope_pairs

    v = frozenset({"fleet", "$public", "team"})
    pairs, dropped = scope_pairs(v)
    assert pairs == scope_pairs(v)[0]                      # deterministic
    assert dropped == 0
    same = [p for p in pairs if p[0] == p[1]]
    assert list(pairs[:len(same)]) == same                 # same-scope first
    assert same == sorted(same)                            # ascending within the group


def test_a_wide_viewer_is_served_and_told_what_was_dropped() -> None:  # type: ignore[no-untyped-def]
    """Refusing a legitimately broad viewer is also an authorization decision, so a
    wide viewer gets a declared partial answer rather than nothing (#214 rev 6).

    The count compares against what the viewer's FULL scope set implies — dropping
    whole SCOPES must not hide inside a report about dropped PAIRS, which is the
    shape that understates truncation exactly when it is largest."""
    from kawa.retrieval import MAX_VIEWER_SCOPES, scope_pairs

    wide = frozenset(f"s{i:02d}" for i in range(MAX_VIEWER_SCOPES + 4))
    pairs, dropped = scope_pairs(wide)
    assert pairs                                            # served, not refused
    assert dropped > 0                                      # and it says so
    full = (len(wide) + 1) ** 2                             # +1: the $public sentinel
    assert dropped == full - len(pairs)


def test_one_leaf_per_pair_covers_every_relation_in_one_seek(conn) -> None:  # type: ignore[no-untyped-def]
    """The correctness requirement is that relation priority PRECEDES hlc in the
    traversal order — not that the query be partitioned by relation. `relation_rank`
    carries it into the index, so a single seek per pair returns every relation
    already in global ranking order.

    Measured, planning cost per expanded node against a table with no matching rows:

        |V|=1     22 -> 2 leaves      6.0 -> 2.2 ms
        |V|=8  1,408 -> 128 leaves  243.4 -> 15.0 ms
    """
    from kawa.retrieval import scope_pairs

    _hub(conn, 30)
    pairs, _ = scope_pairs(frozenset({"fleet"}))
    # {fleet} + the $public sentinel: two scopes, so two same-scope pairs and two
    # cross pairs. The sentinel is not optional -- an envelope-v1 event is visible to
    # everyone, and equality can only say so if every viewer is treated as holding it.
    assert set(pairs) == {("$public", "$public"), ("fleet", "fleet"),
                          ("$public", "fleet"), ("fleet", "$public")}

    with conn.cursor() as cur:
        cur.execute("SET enable_seqscan = off")
        cur.execute(
            "EXPLAIN (COSTS OFF) SELECT relation, target_ref FROM event_links "
            "WHERE source_ref=%s AND resolved AND target_scope_ref=%s AND asserter_scope_ref=%s "
            "ORDER BY relation_rank, target_hlc_phys DESC, target_hlc_logical DESC, "
            "         target_origin_node COLLATE \"C\" DESC, target_ref COLLATE \"C\" DESC LIMIT 20",
            ("e-any", "fleet", "fleet"))
        plan = "\n".join(r[0] for r in cur.fetchall())

    assert "event_links_out_ranked_idx" in plan, plan
    assert "Sort" not in plan, plan
    index_cond = "\n".join(l for l in plan.splitlines() if "Index Cond" in l)
    assert "target_scope_ref" in index_cond and "asserter_scope_ref" in index_cond, plan
    assert not any("scope_ref" in l for l in plan.splitlines() if "Filter:" in l), plan


def test_an_invisible_flood_cannot_erase_the_visible_graph(conn) -> None:  # type: ignore[no-untyped-def]
    """Shadow censorship, which is what #222 round 1 established this whole design is
    for (#212 is the same defect one dimension over).

    A low-privilege actor emits a thousand edges toward a widely-referenced public node
    from a scope only it can read, at the most recent HLCs so they sort first. Under a
    filtered join those rows enter the candidate window and are discarded afterwards,
    so an ordinary viewer's legitimate edges are pushed out and the node reads as
    isolated. No privilege was escalated and nothing was forged -- the attacker simply
    spent the viewer's window.

    Scoped leaves make the attack structurally inert rather than merely expensive: a
    leaf naming (endpoint_scope, asserter_scope) as equality keys cannot touch a row
    outside that pair, so there is no window to spend."""
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    claim = k.record_claim("the contested public fact")
    mine = k.record_observation("legitimate", value_bool=True, method="http_probe")
    k.assert_link(mine.event_id, "supports", claim.event_id)
    conn.commit()

    with conn.cursor() as cur:                       # the flood, at the newest HLCs
        # The ENDPOINT scope is one the viewer holds. Only the ASSERTION is restricted
        # -- public events, related to each other by someone the viewer cannot see.
        # An earlier cut of this test gave the flood a restricted endpoint scope too,
        # so it was excluded by the endpoint key and passed with the asserter key
        # deleted: it asserted the defence while never exercising the dimension #222
        # exists for. Verified by mutation before it was trusted.
        cur.executemany(
            "INSERT INTO event_links(source_ref, relation, target_ref, resolved,"
            " asserted_by_event_id, target_scope_ref, asserter_scope_ref,"
            " source_scope_ref, source_hlc_phys, source_hlc_logical, source_origin_node)"
            " VALUES (%s,'supports',%s,true,'attacker','fleet','secret-attacker',"
            "         'fleet', 99999999999, %s, 'zzz')",
            [(f"flood-{i}", claim.event_id, i) for i in range(1000)])
    conn.commit()

    pairs, _ = scope_pairs(FLEET)
    with conn.cursor() as cur:
        rows, saturated = candidate_edges(cur, claim.event_id, pairs, 20)
    others = {r[1] for r in rows}
    assert mine.event_id in others                   # the legitimate edge survives
    assert not any(o.startswith("flood-") for o in others)
    assert not saturated                             # and the flood never even competed


def test_high_visible_degree_is_bounded_by_the_cap_and_says_so(conn) -> None:  # type: ignore[no-untyped-def]
    """The honest half: a node with genuinely more visible neighbours than the cap is
    truncated, and the Bundle says which node and how hard. A bounded traversal that
    stays silent reads as a complete answer, which is exactly what #212 was."""
    anchor = _hub(conn, 60)
    bundle = retrieve(conn, Intent(about=anchor.event_id, limit=12), viewer_scopes=FLEET)

    t = bundle.candidate_truncation
    assert t is not None and t.nodes >= 1
    assert t.worst_node == anchor.event_id           # the hub, not one of its leaves
    assert t.per_node == CANDIDATE_PER_NODE          # interpretable without reading code
    assert t.scope_pairs_dropped == 0                # the viewer was served whole

    # identical requests produce an identical report, or the Bundle cannot be cited
    again = retrieve(conn, Intent(about=anchor.event_id, limit=12), viewer_scopes=FLEET)
    assert again.candidate_truncation == t


def test_a_saturating_cap_cannot_drop_one_side_of_the_evidence(conn) -> None:  # type: ignore[no-untyped-def]
    """Lock 2 through the mechanism that fixed the fan-out.

    `relation_rank` orders each leaf by relation priority, and `contradicts` (rank 1)
    precedes `supports` (rank 2). A node with more contradictions than the cap
    therefore fills its leaf entirely with contradictions and returns no support at
    all -- a biting cap silently keeping one side, which is the thing Lock 2 names.
    The priority ordering is right; it is just not the governing rule across this one
    boundary, so a missing fair-share relation gets its own bounded seek."""
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    claim = k.record_claim("the disputed fact")
    support = k.record_observation("for", value_bool=True, method="http_probe")
    k.assert_link(support.event_id, "supports", claim.event_id)
    for i in range(CANDIDATE_PER_NODE * 2):          # comfortably past the cap
        against = k.record_observation(f"against_{i}", value_bool=False, method="http_probe")
        k.assert_link(against.event_id, "contradicts", claim.event_id)
    conn.commit()

    pairs, _ = scope_pairs(FLEET)
    with conn.cursor() as cur:
        rows, saturated = candidate_edges(cur, claim.event_id, pairs, CANDIDATE_PER_NODE)
    assert saturated                                  # the cap really did bite
    assert support.event_id in {r[1] for r in rows}   # ...and support survived it
    assert {r[0] for r in rows} >= {"supports", "contradicts"}


def test_expansion_walks_the_canonical_order_not_its_reverse(conn) -> None:  # type: ignore[no-untyped-def]
    """The one ordering rule (#214 step 1), pinned on the path that actually expands.

    The hand-rolled key this replaces sorted ASCENDING while the canonical order --
    and the leaf SQL that does the truncating -- is DESCENDING. That was
    self-consistent while nothing was truncated and incoherent the moment a cap bit:
    the leaf kept the NEWEST rows and expansion then walked the OLDEST of them first,
    so which edges appeared and the order they appeared in disagreed about direction.

    The hlc lint cannot catch this. Its pattern looks for the token `hlc` near a
    `key=`, and after sql/0026 denormalised the stamp the components are columns
    named `hlc_phys`/`hlc_logical` -- bound to locals called `phys`/`logical` by the
    time they reach a sort. The lint was widened for the column names, but the
    binding step is out of a regex's reach, so the invariant is asserted here
    instead (#224 review round 1, finding 3)."""
    from kawa.domain.ids import parse_hlc

    anchor = _hub(conn, 6)
    bundle = retrieve(conn, Intent(about=anchor.event_id, limit=30), viewer_scopes=FLEET)
    records = next(v for k, v in bundle.sections.items() if k.endswith("-neighborhood"))
    assert len(records) > 1, "fixture must expand more than one neighbour"

    # The order under test is the one `_exec_neighborhood` PRODUCED. An earlier cut of
    # this test re-sorted the rows itself and then asserted the result was sorted --
    # tautological, and it passed with the production sort flipped to ascending.
    with conn.cursor() as cur:
        cur.execute("SELECT event_id, hlc, origin_node FROM events WHERE event_id = ANY(%s)",
                    ([r.ref for r in records],))
        stamp = {eid: (parse_hlc(h)[0], parse_hlc(h)[1], node) for eid, h, node in cur.fetchall()}

    keys = [stamp[r.ref] for r in records if r.ref in stamp]
    assert keys == sorted(keys, reverse=True), keys      # newest first, never oldest
    assert keys != sorted(keys), "the fixture cannot distinguish the two directions"


@pytest.mark.parametrize("saturating,expected", [
    ([(3, "node"), (3, "node_1")], "node"),      # a prefix tie: the SHORTER ref wins
    ([(3, "node_1"), (3, "node")], "node"),      # ...whichever order it arrives in
    ([(2, "aaa"), (5, "zzz")], "zzz"),           # count dominates the ref
    ([(4, "b"), (4, "a"), (4, "c")], "a"),
])
def test_the_worst_node_tie_breaks_on_ref_ascending(saturating, expected) -> None:  # type: ignore[no-untyped-def]
    """A pure function, tested as one, because the fixture that would exercise it
    through `retrieve` needs two equally-saturated nodes with prefix-related refs --
    and event refs are fixed-length digests, so the case only arises for the
    human-chosen refs a plan or work anchor carries.

    The cut this replaces inverted the ref with `tuple(-ord(c) for c in ref)` and took
    the max. Tuple comparison runs out of elements while still tied on a prefix, and
    the SHORTER tuple compares smaller, so it returned the LONGER ref -- the opposite
    of the documented rule (#224 review round 1, finding 2)."""
    from kawa.retrieval import _worst

    assert _worst(saturating)[1] == expected


def test_the_expansion_key_is_total_so_two_replicas_cannot_disagree(conn) -> None:  # type: ignore[no-untyped-def]
    """Two nodes holding identical events must produce identical Bundles.

    The tie that broke this is easy to build and was not obvious: an out-edge A->B
    and an in-edge B->A both carry B's stamp, because each takes its ordering
    components from the endpoint being STEPPED TO, which is B either way. They also
    share `relation` and `other`. So they agree on every component of the sort key
    and differ only in `dir` -- which was in neither key.

    `list(set(...))` plus a stable sort then preserved their `set` iteration order,
    and that varies with the process hash seed. Replica A got `path` "A -supports-> B"
    and replica B got "A <-supports- B" from the same log (#224 review round 2,
    finding 1). The fix is not a tie-break bolted on: `other` was passed as the unique
    key and `other` is not unique. The unique key of a traversal candidate is
    (other, direction).

    Asserted as an invariant on the KEY rather than by running under two hash seeds,
    because a total order is the property that makes the seed irrelevant."""
    from kawa.domain.ids import hlc_parts_sort_key

    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    a = k.record_claim("node A")
    b = k.record_claim("node B")
    k.assert_link(a.event_id, "supports", b.event_id)     # out-edge from A
    k.assert_link(b.event_id, "supports", a.event_id)     # in-edge to A, same stamp
    conn.commit()

    pairs, _ = scope_pairs(FLEET)
    with conn.cursor() as cur:
        rows, _sat = candidate_edges(cur, a.event_id, pairs, CANDIDATE_PER_NODE)
    edges = {(rel, other, direction, phys, logical, node_id)
             for rel, other, direction, _leaf, phys, logical, node_id in rows}
    assert len({(e[1], e[2]) for e in edges}) == 2, "fixture needs both directions"

    keys = [hlc_parts_sort_key(p, l, n or "", (o, d)) for _r, o, d, p, l, n in edges]
    assert len(set(keys)) == len(keys), keys              # total: no two edges tie
