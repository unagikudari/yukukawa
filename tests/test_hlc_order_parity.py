"""The two engines must agree on the one causal order (#214 step 1).

A shared definition is necessary and not sufficient. `hlc_order_sql` and
`hlc_sort_key` are generated from one place, but they are executed by
PostgreSQL and by CPython, and those can still disagree — on collation for the
tiebreak, on 64-bit boundaries, on how DESC treats equal keys. The lint pins the
spelling; this pins the behaviour.

The failing case that motivated it: `hlc` is TEXT, so `ORDER BY hlc` puts
"1.10.n" BELOW "1.2.n" while the Python key, parsing ints, puts it above. A
window selected by one and ranked by the other drops rows the ranking wanted.
"""
from __future__ import annotations

import os
import random

import pytest

from kawa.domain.ids import hlc_order_sql, hlc_parts_sort_key, hlc_sort_key

psycopg = pytest.importorskip("psycopg")


@pytest.fixture()
def cur():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cursor:
        cursor.execute("CREATE TEMP TABLE hlc_probe (ref text, hlc text, tiebreak text)")
        yield cursor
    c.rollback()
    c.close()


def _both_orders(cur, rows, desc=True, limit=None):  # type: ignore[no-untyped-def]
    """Return (sql_order, python_order) as ref lists — the comparison this file exists for."""
    cur.execute("TRUNCATE hlc_probe")
    cur.executemany("INSERT INTO hlc_probe (ref, hlc, tiebreak) VALUES (%s,%s,%s)", rows)
    order = hlc_order_sql(tiebreak="tiebreak", unique="ref", desc=desc)
    sql = f"SELECT ref FROM hlc_probe ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    cur.execute(sql)
    from_sql = [r[0] for r in cur.fetchall()]

    ordered = sorted(rows, key=lambda r: hlc_sort_key(r[1], r[2], r[0]), reverse=desc)
    from_python = [r[0] for r in ordered][:limit] if limit is not None else [r[0] for r in ordered]
    return from_sql, from_python


def test_the_case_that_started_this(cur) -> None:  # type: ignore[no-untyped-def]
    """Lexical text order puts 1.10 below 1.2; the causal order does not."""
    rows = [("a", "1.2.nodeA", "nodeA"), ("b", "1.10.nodeA", "nodeA"),
            ("c", "1.9.nodeA", "nodeA"), ("d", "2.1.nodeA", "nodeA")]
    from_sql, from_python = _both_orders(cur, rows)
    assert from_sql == from_python
    assert from_sql[:2] == ["d", "b"]                 # 2.1 then 1.10 — not 1.9


def test_ties_on_physical_and_logical_break_on_the_third_component(cur) -> None:  # type: ignore[no-untyped-def]
    """Two nodes stamping the same (physical, logical) is ordinary in a
    distributed log. Without the tiebreak the order is a preorder and the two
    engines are free to disagree — which is exactly how the third component got
    dropped once already."""
    rows = [(f"n{i}", "5.5.node", f"node{i}") for i in range(10)]
    random.Random(0).shuffle(rows)
    from_sql, from_python = _both_orders(cur, rows)
    assert from_sql == from_python


def test_tiebreak_collation_agrees(cur) -> None:  # type: ignore[no-untyped-def]
    """The tiebreak is text, so a database collation that is not code-point
    order would diverge from Python's `sorted` on exactly these inputs."""
    names = ["Node", "node", "NODE", "node-1", "node_1", "node.1", "node 1", "nodeA", "nodea"]
    rows = [(n, "7.0.x", n) for n in names]
    from_sql, from_python = _both_orders(cur, rows)
    assert from_sql == from_python


def test_bigint_boundaries_agree(cur) -> None:  # type: ignore[no-untyped-def]
    """`parse_hlc` bounds both numeric fields to the signed-64 domain the store
    commits to; the ordering must agree at the edges rather than near them."""
    big = 2**63 - 1
    rows = [("max", f"{big}.{big}.n"), ("zero", "0.0.n"), ("one", "1.0.n"),
            ("near", f"{big - 1}.0.n")]
    rows = [(r[0], r[1], "n") for r in rows]
    from_sql, from_python = _both_orders(cur, rows)
    assert from_sql == from_python
    assert from_sql[0] == "max"


@pytest.mark.parametrize("desc", [True, False])
@pytest.mark.parametrize("seed", range(12))
def test_randomised_parity_including_limit(cur, seed, desc) -> None:  # type: ignore[no-untyped-def]
    """Property-style: the cases that have bitten here are the ones nobody
    thought to write down. LIMIT is included because a top-N is the shape the
    candidate windows in #214 actually use — a full-order match that diverges
    under LIMIT would still drop winners."""
    rnd = random.Random(seed)
    rows = []
    for i in range(60):
        phys = rnd.choice([0, 1, 2, 9, 10, 11, 100, 1_000_000_000_000, 2**62])
        logical = rnd.choice([0, 1, 2, 9, 10, 99])
        node = rnd.choice(["a", "B", "node-1", "node_2", "Node.3", "zz", "0"])
        rows.append((f"r{i}", f"{phys}.{logical}.{node}", node))
    from_sql, from_python = _both_orders(cur, rows, desc=desc)
    assert from_sql == from_python
    for limit in (1, 3, 17):
        s, p = _both_orders(cur, rows, desc=desc, limit=limit)
        assert s == p, f"top-{limit} diverged"


def test_a_malformed_stamp_is_refused_rather_than_positioned() -> None:  # type: ignore[no-untyped-def]
    """Inventing a sort position for an unparseable stamp would be a second
    ordering rule — the thing this module exists to prevent. Admissibility
    (#145) rejects these long before anything orders by them."""
    for bad in ("", "1.2", "x.2.n", "1.x.n", "1.2.", "١٢٣.0.n", f"{2**63}.0.n"):
        with pytest.raises(ValueError):
            hlc_sort_key(bad, "n")


def test_without_a_unique_component_the_order_is_only_a_preorder(cur) -> None:  # type: ignore[no-untyped-def]
    """Found by the randomised case above on its first run, and worth pinning:
    three components tie whenever two events share an origin, a millisecond and
    a logical counter. Both engines then produce a valid order and they need not
    be the same one. `unique=` is what closes it, so a caller that needs
    reproducibility has to say so."""
    rows = [(f"r{i}", "5.5.node", "node") for i in range(12)]
    cur.execute("TRUNCATE hlc_probe")
    cur.executemany("INSERT INTO hlc_probe (ref, hlc, tiebreak) VALUES (%s,%s,%s)", rows)

    cur.execute(f"SELECT ref FROM hlc_probe ORDER BY {hlc_order_sql(tiebreak='tiebreak', unique=None)}")
    without = [r[0] for r in cur.fetchall()]
    assert sorted(without) == sorted(r[0] for r in rows)      # a valid order — just not pinned

    order = hlc_order_sql(tiebreak="tiebreak", unique="ref")
    cur.execute(f"SELECT ref FROM hlc_probe ORDER BY {order}")
    with_unique = [r[0] for r in cur.fetchall()]
    assert with_unique == [r[0] for r in
                           sorted(rows, key=lambda r: hlc_sort_key(r[1], r[2], r[0]), reverse=True)]


def test_non_ascii_tiebreaks_agree(cur) -> None:  # type: ignore[no-untyped-def]
    """Review round 1: without an explicit collation the database sorts text by
    locale. Measured on this fleet's ja_JP.UTF-8 database, these six values come
    back in a different order from Python's — so `hlc_order_sql` pins COLLATE
    "C", which is code-point order, which is what `sorted` does."""
    names = ["ノード", "nodeZ", "Ω", "é", "e", "Z"]
    rows = [(n, "3.0.x", n) for n in names]
    from_sql, from_python = _both_orders(cur, rows)
    assert from_sql == from_python


def test_a_negative_component_is_refused_rather_than_ordered() -> None:  # type: ignore[no-untyped-def]
    """`split_part('-1.0.n','.',1)::bigint` yields -1 in Postgres while
    parse_hlc rejects it — an input the two engines disagree about at the level
    of whether it can be ordered at all. Admissibility refuses it upstream; this
    pins that the Python side does not quietly accept what SQL would take."""
    with pytest.raises(ValueError):
        hlc_sort_key("-1.0.n", "n")


def test_unique_has_no_default() -> None:  # type: ignore[no-untyped-def]
    """A default here is the unsafe direction, and review round 1 found every
    refactored call site had silently taken it. Omitting the argument must be a
    TypeError, so the preorder can only be chosen out loud."""
    with pytest.raises(TypeError):
        hlc_order_sql()          # type: ignore[call-arg]
    assert hlc_order_sql(unique=None)                      # deliberate preorder is expressible
    assert "COLLATE" in hlc_order_sql(unique="event_id")   # and text is code-point ordered


def test_the_causal_index_serves_the_collated_order(cur) -> None:  # type: ignore[no-untyped-def]
    """A b-tree index only serves an ORDER BY whose collation MATCHES. Pinning
    COLLATE "C" in the queries without rebuilding `events_hlc_causal_idx` turned
    the brief's index tail read back into a full scan — the regression 0016
    existed to remove, reintroduced by the fix for a different divergence
    (review round 2). 0025 rebuilds it; this keeps the two in step.

    Asserted through EXPLAIN, not timing: a slow query is noise, a `Sort` node is
    the mechanism. `enable_seqscan=off` asks the planner whether the index CAN
    serve the order, which is the real question — on a small table it would
    reasonably prefer a scan either way, and the answer would say nothing.

    The mismatched case is asserted too, so a test that can no longer fail is
    itself detectable."""
    cur.execute("SET enable_seqscan = off")

    matched = hlc_order_sql(unique="event_id")
    cur.execute(f"EXPLAIN (COSTS OFF) SELECT hlc FROM events ORDER BY {matched} LIMIT 1")
    plan = "\n".join(r[0] for r in cur.fetchall())
    assert "events_hlc_causal_idx" in plan, plan
    assert "Sort" not in plan, plan

    mismatched = matched.replace(' COLLATE "C"', "")
    cur.execute(f"EXPLAIN (COSTS OFF) SELECT hlc FROM events ORDER BY {mismatched} LIMIT 1")
    plan = "\n".join(r[0] for r in cur.fetchall())
    assert "Sort" in plan, "the collation mismatch must still cost a Sort — " + plan


def test_the_index_definition_matches_the_helper(cur) -> None:  # type: ignore[no-untyped-def]
    """DDL is the one place the ordering must be written a second time — an index
    cannot call a Python function. That is the drift this whole step exists to
    stop, so the duplication is pinned rather than trusted: the index is read
    back from the catalogue and compared against what the helper generates.

    Without this, `hlc_order_sql` could gain or reorder a component and the index
    would silently stop serving it — which is review round 2's regression,
    arriving later and from the other side.

    Compared as a SEQUENCE, not as a set of parts: an index whose components are
    all present but in another order serves nothing."""
    import re as _re

    cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname='events_hlc_causal_idx'")
    row = cur.fetchone()
    assert row, "events_hlc_causal_idx is missing"

    def norm(text: str) -> str:
        # the catalogue adds its own casts and parentheses; neither changes the order
        return _re.sub(r"[\s()]", "", text.replace("::text", "").replace("::bigint", ""))

    assert norm(hlc_order_sql(unique="event_id")) in norm(row[0]), (
        f"the index no longer matches hlc_order_sql:\n  helper: "
        f"{hlc_order_sql(unique='event_id')}\n  index : {row[0]}")


def test_parity_holds_for_the_denormalised_components_too(cur) -> None:  # type: ignore[no-untyped-def]
    """The parity mechanism was validating a function production does not call.

    `hlc_sort_key` takes a stamp string, and every caller of it is in this file. The
    hot path -- neighborhood expansion -- reads `hlc_phys`/`hlc_logical` columns off
    the link row (sql/0026) and orders by `hlc_parts_sort_key`, which nothing here
    compared against SQL. So the guarantee "the two engines agree" covered the entry
    point with no production callers and not the one with all of them.

    This compares the ORDER BY the leaf query actually issues against the Python key
    the expansion actually uses, over the same rows."""
    # NO all-NULL row here, and that absence is the finding. SQL reads `hlc_phys DESC`
    # NULLS FIRST (the index is built that way, and `NULLS LAST` would stop matching
    # it and bring back the Sort #222 removed); `hlc_parts_sort_key` puts an absent
    # endpoint LAST. The two engines genuinely disagree on that row -- so sql/0031
    # makes the row impossible instead of arguing that no current writer creates it.
    rows = [("a", 1, 2, "nodeA"), ("b", 1, 10, "nodeA"), ("c", 1, 9, "nodeB"),
            ("d", 2, 1, "nodeA"), ("e", 0, 0, "nodeZ")]       # phys=0 is a HELD stamp
    cur.execute("CREATE TEMP TABLE parts_probe "
                "(ref text, hlc_phys bigint, hlc_logical bigint, origin_node text)")
    cur.executemany("INSERT INTO parts_probe VALUES (%s,%s,%s,%s)", rows)

    # verbatim the ordering the leaf query issues (kawa.retrieval._LEAF_SQL)
    cur.execute('SELECT ref FROM parts_probe ORDER BY hlc_phys DESC, '
                'hlc_logical DESC, origin_node COLLATE "C" DESC, ref COLLATE "C" DESC')
    from_sql = [r[0] for r in cur.fetchall()]

    from_python = [r[0] for r in sorted(
        rows, key=lambda r: hlc_parts_sort_key(r[1], r[2], r[3] or "", r[0]), reverse=True)]

    assert from_sql == from_python, (from_sql, from_python)
    assert from_sql[-1] == "e"          # phys=0 is a real stamp, ordered as one


def test_an_endpoint_cannot_have_a_scope_without_a_stamp(cur) -> None:  # type: ignore[no-untyped-def]
    """sql/0031, which is what lets the parity above be a guarantee rather than an
    argument about the current write paths."""
    import psycopg
    cur.execute("SAVEPOINT p")
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("INSERT INTO event_links(source_ref, relation, target_ref, resolved,"
                    " asserted_by_event_id, target_scope_ref, asserter_scope_ref,"
                    " source_scope_ref) VALUES"
                    " ('s','supports','t',true,'a','$public','$public','fleet')")
    cur.execute("ROLLBACK TO p")
