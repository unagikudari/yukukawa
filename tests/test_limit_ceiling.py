"""`Intent.limit` is a hard ceiling (#214 step 2, from #211).

Before this, `limit` was a floor the planner could enlarge: `divmod(max(limit, n), n)`
raised any limit below the class count, and the Phase-A sideband was appended outside
the pool entirely. Measured on `main` before the change:

    claim_event  limit=0  -> 14 rows authorised
    claim_event  limit=1  -> 14
    claim_event  limit=5  -> 14

So a resource policy asking for nothing was granted a plan sized like one that asked
for fourteen, and no caller-visible number described that total. These pin the
property across every shape and every small limit, because the failures that motivated
this are all at the small end where the old arithmetic quietly rounded up.
"""
from __future__ import annotations

import os

import pytest

from kawa.retrieval import (FLEET_SCOPES, Intent, compile_plan, resolve_bindings)

psycopg = pytest.importorskip("psycopg")

FLEET = FLEET_SCOPES
LIMITS = [0, 1, 2, 3, 4, 5, 7, 8, 13, 21, 59, 100]


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    yield c
    c.rollback()
    c.close()


def _plans(conn, limit):  # type: ignore[no-untyped-def]
    """One plan per intent SHAPE — the ceiling must hold for all of them, and the
    shapes differ in class count, which is exactly what the old arithmetic keyed on."""
    shapes = {
        "unbound-ref": Intent(about="e-does-not-exist", limit=limit),
        "textual": Intent(text_terms="replication mesh", limit=limit),
        "textual+fallback": Intent(text_terms="mesh", fallback_policy=3, limit=limit),
        "empty": Intent(limit=limit),
    }
    return {name: compile_plan(resolve_bindings(conn, i, viewer_scopes=FLEET))
            for name, i in shapes.items()}


@pytest.mark.parametrize("limit", LIMITS)
def test_total_budget_never_exceeds_the_limit(conn, limit) -> None:  # type: ignore[no-untyped-def]
    for name, plan in _plans(conn, limit).items():
        assert plan.total_budget <= limit, f"{name} at limit={limit}: {plan.total_budget}"


@pytest.mark.parametrize("limit", LIMITS)
def test_no_class_is_planned_with_a_zero_budget(conn, limit) -> None:  # type: ignore[no-untyped-def]
    """A zero-budget class would run a query that cannot contribute a row. Classes
    the ceiling could not reach belong in `skipped_at_compile`, not in the plan."""
    for name, plan in _plans(conn, limit).items():
        assert all(q.budget >= 1 for q in plan.query_classes), name


@pytest.mark.parametrize("limit", LIMITS)
def test_every_class_is_either_planned_or_reported(conn, limit) -> None:  # type: ignore[no-untyped-def]
    """The distinction that matters to a reader: 'not affordable' must never look
    like 'does not exist'. Every class the shape would plan is accounted for."""
    for name, plan in _plans(conn, limit).items():
        planned = {q.purpose for q in plan.query_classes}
        reported = {s.purpose for s in plan.skipped_at_compile}
        assert not (planned & reported), f"{name}: a class is both planned and skipped"
        assert all(s.reason == "tier_budget_exhausted" for s in plan.skipped_at_compile)


def test_limit_zero_authorises_nothing(conn) -> None:  # type: ignore[no-untyped-def]
    """The headline case. `limit=0` used to authorise fourteen records."""
    for name, plan in _plans(conn, 0).items():
        assert plan.query_classes == (), name
        assert plan.total_budget == 0, name


def test_unplannable_and_unaffordable_are_different_silences(conn) -> None:  # type: ignore[no-untyped-def]
    """Two ways to return nothing, and only one of them is a budget statement.

    A ref that binds to no anchor and carries no text has NO plannable class — there
    is nothing the ceiling declined, so an empty skip list is the honest answer. A
    shape that would have planned classes and could not afford them must say so, or a
    reader cannot tell 'unaffordable' from 'absent' — which is the distinction this
    whole step exists to make legible."""
    plans = _plans(conn, 0)
    assert plans["unbound-ref"].skipped_at_compile == ()      # nothing was plannable
    assert plans["empty"].skipped_at_compile == ()
    assert plans["textual"].skipped_at_compile                # plannable, unaffordable
    assert all(s.reason == "tier_budget_exhausted"
               for s in plans["textual"].skipped_at_compile)


def test_a_small_limit_costs_reach_not_grounding(conn, ) -> None:  # type: ignore[no-untyped-def]
    """Tiering exists for this: the caller who sets a limit to be careful is exactly
    the one who must not lose the anchor. What falls off the end is expansive."""
    plan = _plans(conn, 1)["textual"]
    assert [q.purpose for q in plan.query_classes] == ["lexical"] or \
           [q.purpose for q in plan.query_classes] == ["anchor_lookup"]
    dropped = {s.purpose for s in plan.skipped_at_compile}
    assert "vector" in dropped                       # reach goes first


def test_the_plan_states_the_ceiling_it_enforces(conn) -> None:  # type: ignore[no-untyped-def]
    """A caller should be able to read what it will be held to, rather than infer
    it from the result it gets back."""
    plan = _plans(conn, 7)["textual"]
    assert plan.result_limit == 7
    assert plan.total_budget <= plan.result_limit


@pytest.mark.parametrize("limit", LIMITS)
def test_apportionment_is_reproducible(conn, limit) -> None:  # type: ignore[no-untyped-def]
    """Same intent, same catalogue state, same plan — byte-identical, including the
    skip list. Determinism is what makes the ceiling auditable."""
    assert _plans(conn, limit) == _plans(conn, limit)
