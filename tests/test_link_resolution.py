"""Deferred link resolution is a legal transition (#215).

The append-only guard on `event_links` and the resolution backfill in
`reducers.reduce` contradicted each other, and coexisted only because the
backfill had always matched zero rows — the trigger is FOR EACH ROW, so it
never fired. These tests exercise the path that was never exercised: a link
asserted before its target exists, resolving when the target arrives.

The guard is tested by what it permits and refuses, not by its message text.
"""
from __future__ import annotations

import os

import pytest

psycopg = pytest.importorskip("psycopg")


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute("TRUNCATE event_links")
    c.commit()
    yield c
    c.rollback()
    c.close()


def _pending(cur, source="s1", target="t1", relation="supports", asserted="a1"):  # type: ignore[no-untyped-def]
    """A link whose target this node does not hold yet."""
    cur.execute("INSERT INTO event_links(source_ref, relation, target_ref, resolved, "
                "asserted_by_event_id) VALUES (%s,%s,%s,false,%s)",
                (source, relation, target, asserted))


def _resolve(cur, target="t1"):  # type: ignore[no-untyped-def]
    """Exactly the statement reducers.reduce runs on every ingested event."""
    cur.execute("UPDATE event_links SET resolved = true "
                "WHERE target_ref = %s AND NOT resolved", (target,))
    return cur.rowcount


# ---- what the guard must PERMIT ----

def test_a_pending_link_resolves_when_its_target_arrives(conn) -> None:  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        _pending(cur)
        assert _resolve(cur) == 1
        cur.execute("SELECT resolved FROM event_links WHERE source_ref='s1'")
        assert cur.fetchone()[0] is True
    conn.commit()


def test_several_pending_links_for_one_target_resolve_together(conn) -> None:  # type: ignore[no-untyped-def]
    """One arriving event may satisfy many links — the backfill is one statement,
    so a per-row guard has to permit every row of it."""
    with conn.cursor() as cur:
        for i in range(5):
            _pending(cur, source=f"s{i}", asserted=f"a{i}")
        assert _resolve(cur) == 5
    conn.commit()


def test_a_future_column_is_permitted_by_default(conn) -> None:  # type: ignore[no-untyped-def]
    """#214 step 4 denormalises endpoint scope and HLC ordering onto this row on
    the same transition, so a resolution that also sets other columns must be
    legal — otherwise that step needs a second rewrite of an append-only guard.

    Pinned structurally rather than by adding a column (the test role does not
    own the table, and a DDL-in-a-test would be worse than the assertion it
    buys): the guard must name the assertion columns EXPLICITLY and reject on
    that list, so anything not on it is allowed. A guard rewritten to compare
    whole rows, or to enumerate permitted columns instead, would silently make
    the next denormalised column illegal — and would pass every other test
    here."""
    with conn.cursor() as cur:
        cur.execute("SELECT prosrc FROM pg_proc WHERE proname='kawa_link_resolution_guard'")
        row = cur.fetchone()
    assert row, "the resolution guard is not installed"
    src = row[0]

    for assertion_column in ("source_ref", "relation", "target_ref", "asserted_by_event_id"):
        assert assertion_column in src, assertion_column      # named, so it is protected
    # a whole-row comparison would protect the future column too, i.e. forbid it
    assert "NEW IS DISTINCT FROM OLD" not in src.replace("  ", " ")
    assert "NEW.*" not in src


# ---- what the guard must still REFUSE ----

def test_resolution_does_not_run_backwards(conn) -> None:  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        _pending(cur)
        _resolve(cur)
    conn.commit()
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute("UPDATE event_links SET resolved = false WHERE source_ref='s1'")
    conn.rollback()


def test_an_already_resolved_link_cannot_be_rewritten(conn) -> None:  # type: ignore[no-untyped-def]
    """Once resolved, the row is closed to every UPDATE — which is what makes
    'a resolution may populate NULL columns' safe without inspecting them: the
    transition can happen at most once per row."""
    with conn.cursor() as cur:
        _pending(cur)
        _resolve(cur)
    conn.commit()
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute("UPDATE event_links SET resolved = true WHERE source_ref='s1'")
    conn.rollback()


@pytest.mark.parametrize("column,value", [
    ("source_ref", "other"), ("relation", "contradicts"),
    ("target_ref", "other"), ("asserted_by_event_id", "other"),
])
def test_the_assertion_stays_immutable_even_during_resolution(conn, column, value) -> None:  # type: ignore[no-untyped-def]
    """Flipping `resolved` must not become a vehicle for rewriting who linked
    what to what — the exception is for derived state only."""
    with conn.cursor() as cur:
        _pending(cur)
    conn.commit()
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute(f"UPDATE event_links SET resolved = true, {column} = %s "  # noqa: S608
                    "WHERE target_ref = 't1' AND NOT resolved", (value,))
    conn.rollback()


def test_delete_is_still_forbidden(conn) -> None:  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        _pending(cur)
    conn.commit()
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute("DELETE FROM event_links WHERE source_ref='s1'")
    conn.rollback()


def test_every_column_is_classified_as_assertion_or_derived(conn) -> None:  # type: ignore[no-untyped-def]
    """The guard protects a NAMED list, so a column added later is mutable
    during the resolution unless someone remembers to add it. Review round 1
    flagged that and recommended a checklist; a checklist is the thing that
    gets skipped, so this is the mechanism instead.

    Every column of `event_links` must be either an assertion column the guard
    names, or explicitly declared derived here. Adding a column without
    classifying it fails — which forces the decision to be made rather than
    defaulted into mutability."""
    DERIVED = {
        "resolved",          # local: does this node hold the endpoint yet (#215)
        # #214 step 4 denormalises these onto the row during the same
        # transition; they are derived from the endpoint event, not asserted.
        "scope_ref", "asserter_scope_ref", "hlc_phys", "hlc_logical", "origin_node",
    }
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='event_links'")
        columns = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT prosrc FROM pg_proc WHERE proname='kawa_link_resolution_guard'")
        src = cur.fetchone()[0]

    unclassified = {c for c in columns - DERIVED if f"NEW.{c}" not in src}
    assert not unclassified, (
        f"event_links columns neither guarded nor declared derived: {sorted(unclassified)}. "
        "Add them to the guard's immutability check, or to DERIVED here with a reason.")
