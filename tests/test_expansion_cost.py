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
from kawa.retrieval import Intent, retrieve

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
    it."""
    with conn.cursor() as cur:
        cur.execute("SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name='event_links' "
                    "  AND column_name IN ('scope_ref','asserter_scope_ref')")
        nullable = {name: flag for name, flag in cur.fetchall()}
    assert nullable == {"scope_ref": "NO", "asserter_scope_ref": "NO"}, nullable


def test_a_scope_pair_leaf_seeks_without_touching_invisible_rows(conn) -> None:  # type: ignore[no-untyped-def]
    """The security property, asserted through the plan rather than the result.

    Both scopes are equality keys, so a pair the viewer does not hold is never
    walked — which is what makes the censorship attack in #222 impossible rather
    than merely unlikely: there is no window an invisible row can consume.

    A `Filter` on either scope column would mean rows are fetched and discarded,
    i.e. exactly that window reopening, so its absence is the assertion."""
    _hub(conn, 30)
    with conn.cursor() as cur:
        cur.execute("SET enable_seqscan = off")
        cur.execute(
            "EXPLAIN (COSTS OFF) SELECT target_ref FROM event_links "
            "WHERE source_ref=%s AND relation='supports' AND resolved "
            "  AND scope_ref=%s AND asserter_scope_ref=%s "
            "ORDER BY hlc_phys DESC, hlc_logical DESC, "
            "         origin_node COLLATE \"C\" DESC, target_ref COLLATE \"C\" DESC LIMIT 5",
            ("e-any", "fleet", "fleet"))
        plan = "\n".join(r[0] for r in cur.fetchall())

    assert "event_links_out_scoped_idx" in plan, plan
    assert "Sort" not in plan, plan

    # Both scopes must appear as INDEX CONDITIONS, never as a Filter: a filtered
    # scope means rows are fetched and discarded, which is the window an invisible
    # row could consume.
    index_cond = "\n".join(line for line in plan.splitlines() if "Index Cond" in line)
    assert "scope_ref" in index_cond and "asserter_scope_ref" in index_cond, plan
    filters = [line for line in plan.splitlines() if "Filter:" in line]
    assert not any("scope_ref" in f for f in filters), f"a scope fell out of the seek:\n{plan}"
