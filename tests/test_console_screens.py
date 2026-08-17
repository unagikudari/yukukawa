"""Step-3 screen tests (plan-console-phase1): the seven invariants extended to
RENDERED output. The Situation and Fleet pages render only what the projections
hold — UNKNOWN stays unknown (never green/red), INCOMPLETE shows its named gap,
STALE derives from measured reachability=CRIT only, absence renders as absence."""
from __future__ import annotations

import inspect
import os
import re

import pytest

from kawa.application.services import Kawa
from kawa.console import render as render_mod
from kawa.console.render import render
from kawa.domain.identity import IdentityContext
from kawa.projections.console_rollup import refresh_console_projections

psycopg = pytest.importorskip("psycopg")

_TABLES = ("content_embedding, event_content, events, event_links, event_link, "
           "event_observation, event_claim, event_plan, event_work, "
           "event_work_dependency, event_work_retired, event_result, "
           "current_claim_standing, current_plans, current_work, "
           "current_work_dependency, runtime_work_occupancy, work_dispatch, "
           "situation_rollup, fleet_node, evidence_provenance, projection_state")


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_TABLES}")
    c.commit()
    yield c
    c.rollback()
    c.close()


def _now() -> str:
    return "2026-08-17T12:00:00Z"


def _kawa(conn):  # type: ignore[no-untyped-def]
    return Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-console"))


def _seed_and_refresh(conn):  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    k.record_claim("screen seed claim")
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    return k


def _set_node(conn, **cells):  # type: ignore[no-untyped-def]
    """Overwrite MEASURED cells on the seeded 'test' node (source/as_of included,
    as the schema CHECKs demand), then re-roll the situation cards."""
    from kawa.projections.console_rollup import refresh_situation_rollup
    with conn.cursor() as cur:
        sets, params = [], []
        for col, val in cells.items():
            sets.append(f"{col}=%s")
            params.append(val)
        cur.execute(f"UPDATE fleet_node SET {', '.join(sets)} WHERE node_ref='test'", params)
        refresh_situation_rollup(cur)
    conn.commit()


def _body(page: str) -> str:
    """The rendered content after the stylesheet — CSS comments mention state
    names, so invariant assertions run against the body only."""
    return page.split("</style>", 1)[1]


def _chips(page: str) -> dict[str, str]:
    """Map chip label (RCH/WKL/REP) -> the full <span class="st ..."> markup."""
    out = {}
    for m in re.finditer(r'<span class="st ([a-z]+)">(RCH|WKL|REP)\b[^<]*</span>', page):
        out[m.group(2)] = m.group(0)
    return out


# --- invariant 1: five dimensions, each its own card, never merged ---

def test_situation_serves_five_cards_never_merged(conn):  # type: ignore[no-untyped-def]
    _seed_and_refresh(conn)
    page = render(conn, "/")
    assert page.count('class="card dim"') == 5
    for label in ("Authority", "Execution", "Epistemic", "Health / Reach",
                  "Projection freshness"):
        assert label in page
    # no synthesised combined field exists to render
    assert "overall" not in page.lower() and "combined" not in page.lower()


# --- invariant 3 + work constraint: INCOMPLETE is first-class and shows its gap ---

def test_incomplete_cards_show_their_named_gap(conn):  # type: ignore[no-untyped-def]
    _seed_and_refresh(conn)
    page = render(conn, "/")
    assert "gap: verifier read-model absent (phase 2+)" in page          # AUTHORITY
    assert "gap: core projections do not report state yet" in page       # FRESHNESS
    assert 'class="st inc">INCOMPLETE' in page                           # hatch class, no spinner
    assert "spinner" not in _body(page).lower()


# --- work constraint: UNKNOWN renders as unknown, never green/red ---

def test_unknown_renders_neutral_never_green_or_red(conn):  # type: ignore[no-untyped-def]
    _seed_and_refresh(conn)
    page = render(conn, "/fleet")
    chips = _chips(page)
    assert set(chips) == {"RCH", "WKL", "REP"}
    for markup in chips.values():
        assert "UNKNOWN" in markup
        assert 'class="st unk"' in markup            # dashed neutral —
        assert '"st ok"' not in markup and '"st crit"' not in markup
    situation = render(conn, "/")
    assert 'class="st unk">UNKNOWN' in situation      # HEALTH_REACH card, same treatment


# --- invariant 6: reachability ≠ freshness — STALE derives from CRIT only ---

def test_crit_reachability_stales_measured_siblings(conn):  # type: ignore[no-untyped-def]
    _seed_and_refresh(conn)
    _set_node(conn, reachability="CRIT", reachability_source="observation:node_probe",
              reachability_as_of=_now(), workload="OK", workload_detail="idle",
              workload_as_of=_now())
    page = render(conn, "/fleet")
    chips = _chips(page)
    assert 'class="st crit"' in chips["RCH"]                       # the measured negative itself
    assert 'class="st stale"' in chips["WKL"]                      # sibling: freshness statement,
    assert "STALE (last OK" in chips["WKL"]                        # keeping the measured value
    assert 'class="st unk"' in chips["REP"] and "STALE" not in chips["REP"]
    # an unmeasured sibling has no value to be stale


def test_unknown_reachability_stales_nothing(conn):  # type: ignore[no-untyped-def]
    _seed_and_refresh(conn)
    _set_node(conn, workload="OK", workload_detail="idle", workload_as_of=_now())
    page = render(conn, "/fleet")
    assert "STALE" not in _body(page)
    assert 'class="st ok"' in _chips(page)["WKL"]


# --- absence ≠ negative: measured negatives surface WITH residue in the render ---

def test_measured_negative_renders_with_residue(conn):  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    k.create_plan("plan-t", "kawa", "seed")
    k.derive_work("w-green", "plan-t", "implement")
    k.derive_work("w-stuck", "plan-t", "implement")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("UPDATE current_work SET execution='blocked' WHERE work_ref='w-stuck'")
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    page = render(conn, "/")
    assert 'class="st warn">ATTENTION' in page
    assert "w-stuck" in page                        # the residue names the item on the card
    # north-star card: big numerator + /denominator (label dropped from markup)
    assert '>1<span class="bigden">/2<' in page


# --- invariant 7: freshness always surfaced, on every card and cell ---

def test_freshness_surfaced_on_every_card(conn):  # type: ignore[no-untyped-def]
    _seed_and_refresh(conn)
    page = render(conn, "/")
    # 5 dimension cards + the co-resident fleet panel (north-star landing composition)
    assert page.count('class="asof"') == 6
    for src in ("current_work", "current_claim_standing", "fleet_node",
                "projection_state", "none (deferred)"):
        assert f"from {src}" in page                # and names its source projection
    fleet = render(conn, "/fleet")
    assert 'class="asof"' in fleet


# --- work constraint: screens read projections only, never raw event tables ---

def test_screens_read_projections_only():  # type: ignore[no-untyped-def]
    projections = {"situation_rollup", "fleet_node", "fleet_node_facet", "current_plans", "current_work"}
    for fn in (render_mod._screen_situation, render_mod._screen_fleet,
               render_mod._header_stats):
        # case-sensitive: SQL in this module writes FROM/JOIN uppercase; prose
        # in docstrings ("from projections") must not count as a table read
        touched = set(re.findall(r"(?:FROM|JOIN)\s+(\w+)", inspect.getsource(fn)))
        assert touched <= projections, f"{fn.__name__} reads outside projections: {touched}"


# --- work constraint: no client-side framework, no CDN, no build step ---

def test_no_scripts_no_external_assets(conn):  # type: ignore[no-untyped-def]
    _seed_and_refresh(conn)
    for path in ("/", "/fleet"):
        page = render(conn, path)
        assert "<script" not in page and "<link" not in page
        assert "src=" not in page                   # nothing fetched to render


# --- expected observation: pages served from live projections; absence is honest ---

def test_pages_served_and_unknown_path_404(conn):  # type: ignore[no-untyped-def]
    _seed_and_refresh(conn)
    assert render(conn, "/") is not None and render(conn, "/fleet") is not None
    assert render(conn, "/no-such-screen") is None


def test_empty_projections_render_absence_not_invention(conn):  # type: ignore[no-untyped-def]
    page = render(conn, "/")
    assert "situation_rollup is empty" in page and 'class="card dim"' not in page
    fleet = render(conn, "/fleet")
    assert "fleet_node is empty" in fleet
    assert "— events" in page                       # ledger tally: absence until a refresh runs


def test_partial_rollup_states_the_missing_dimension(conn):  # type: ignore[no-untyped-def]
    _seed_and_refresh(conn)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM situation_rollup WHERE dimension='EPISTEMIC'")
    conn.commit()
    page = render(conn, "/")
    assert "holds no row for: EPISTEMIC" in page    # absence stated, not painted over
    assert page.count('class="card dim"') == 4      # the four real rows still render


# --- Evidence screen (step 4): chains render, absence renders as absence ---

def _seed_link(conn):  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-screens"))
    a = k.record_claim("evidence chain source")
    b = k.record_claim("evidence chain target")
    k.assert_link(a.event_id, "supports", b.event_id)
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    return a, b


def test_evidence_chain_renders_event_derived_only(conn):  # type: ignore[no-untyped-def]
    a, b = _seed_link(conn)
    page = render(conn, "/evidence", {"ref": [a.event_id]})
    assert page is not None
    assert "supports" in page and a.event_id[:20] in page and b.event_id[:20] in page
    assert "claim.recorded" in page                                # endpoint kinds resolved
    assert "event-derived" in page and "no inferred edge exists" in page


def test_evidence_missing_provenance_is_stated_not_implied(conn):  # type: ignore[no-untyped-def]
    _seed_link(conn)                                               # non-empty projection
    ghost = "sha256:" + "f" * 64
    page = render(conn, "/evidence", {"ref": [ghost]})
    assert page is not None and "Missing provenance" in page
    assert "no grounding chain" in page and "Absence, not an error" in page


def test_evidence_default_view_lists_recent_edges_or_honest_empty(conn):  # type: ignore[no-untyped-def]
    empty = render(conn, "/evidence")
    assert empty is not None and "evidence_provenance is empty" in empty
    _seed_link(conn)
    page = render(conn, "/evidence")
    assert page is not None and "Recent edges (1)" in page
