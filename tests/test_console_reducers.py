"""Step-2 reducer tests (plan-console-phase1): fleet_node + situation_rollup
refresh derives ONLY what the step-1 contract admits — absence stays UNKNOWN,
known gaps stay INCOMPLETE-and-named, measured negatives surface with residue."""
from __future__ import annotations

import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.projections.console_rollup import refresh_console_projections
from kawa.projections.reducers import rebuild

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


def _rollup(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT dimension, state, gap, count_total, count_nongreen, residue "
                    "FROM situation_rollup ORDER BY dimension")
        return {r[0]: r[1:] for r in cur.fetchall()}


def test_empty_log_yields_five_honest_cards_and_no_nodes(conn):  # type: ignore[no-untyped-def]
    refresh_console_projections(conn)
    conn.commit()
    cards = _rollup(conn)
    assert set(cards) == {"AUTHORITY", "EXECUTION", "EPISTEMIC", "HEALTH_REACH",
                          "PROJECTION_FRESHNESS"}                 # exactly five, never merged
    assert cards["AUTHORITY"][0] == "INCOMPLETE"
    assert "verifier read-model absent" in cards["AUTHORITY"][1]  # named gap
    assert cards["EXECUTION"][:1] == ("OK",) and cards["EXECUTION"][2] == 0
    assert cards["EPISTEMIC"][0] == "OK"
    assert cards["HEALTH_REACH"][0] == "UNKNOWN"                  # absence, and NO gap
    assert cards["HEALTH_REACH"][1] is None
    assert cards["PROJECTION_FRESHNESS"][0] == "INCOMPLETE"       # known, named gap
    assert "do not report state yet" in cards["PROJECTION_FRESHNESS"][1]
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM fleet_node")
        assert cur.fetchone()[0] == 0                             # no origins, no rows


def test_real_events_yield_unknown_cells_never_invented_health(conn):  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    k.record_claim("console reducer seed claim A")
    k.record_claim("console reducer seed claim B")
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT node_ref, reachability, reachability_source, workload, "
                    "replication, last_origin_seq FROM fleet_node")
        rows = cur.fetchall()
    assert [r[0] for r in rows] == ["test"]                       # one origin, one row
    node = rows[0]
    assert (node[1], node[3], node[4]) == ("UNKNOWN", "UNKNOWN", "UNKNOWN")
    assert node[2] is None                                        # UNKNOWN carries no source
    assert node[5] == 2                                           # identity facts ARE derived
    cards = _rollup(conn)
    assert cards["EPISTEMIC"][2:4] == (2, 0)                      # two claims, none contested
    assert cards["HEALTH_REACH"][0] == "UNKNOWN"                  # cells all unmeasured


def test_measured_negative_surfaces_with_residue(conn):  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    k.create_plan("plan-t", "kawa", "seed")
    k.derive_work("w-green", "plan-t", "implement")
    k.derive_work("w-stuck", "plan-t", "implement")
    conn.commit()
    # a measured negative in the SOURCE projection (disposable read model):
    with conn.cursor() as cur:
        cur.execute("UPDATE current_work SET execution='blocked' WHERE work_ref='w-stuck'")
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    cards = _rollup(conn)
    state, _gap, total, nongreen, residue = cards["EXECUTION"]
    assert (state, total, nongreen) == ("ATTENTION", 2, 1)
    assert "w-stuck" in residue and "w-green" not in residue      # residue names the item


def test_contested_claim_moves_epistemic_card(conn):  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    a = k.record_claim("claim under attack")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("UPDATE current_claim_standing SET standing='contested' "
                    "WHERE claim_event_id=%s", (a.event_id,))
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    cards = _rollup(conn)
    assert cards["EPISTEMIC"][0] == "ATTENTION" and cards["EPISTEMIC"][3] == 1


def _seed_fleet_and_rollup(conn, **cells):  # type: ignore[no-untyped-def]
    """Measured-negative path: refresh fleet, overwrite one node's MEASURED
    cells (with the source/as_of the schema demands), then roll up."""
    from kawa.projections.console_rollup import refresh_fleet_node, refresh_situation_rollup
    k = _kawa(conn)
    k.record_claim("health seed")
    conn.commit()
    with conn.cursor() as cur:
        refresh_fleet_node(cur)
        sets, params = [], []
        for col, val in cells.items():
            sets.append(f"{col}=%s")
            params.append(val)
        cur.execute(f"UPDATE fleet_node SET {', '.join(sets)} WHERE node_ref='test'", params)
        refresh_situation_rollup(cur)
    conn.commit()
    return _rollup(conn)["HEALTH_REACH"]


def test_health_reach_crit_reachability_is_crit_with_residue(conn):  # type: ignore[no-untyped-def]
    state, gap, total, nongreen, residue = _seed_fleet_and_rollup(
        conn, reachability="CRIT", reachability_source="observation:node_probe",
        reachability_as_of=_now())
    assert (state, total, nongreen) == ("CRIT", 1, 1) and "test" in residue


def test_health_reach_workload_attention_is_not_ok(conn):  # type: ignore[no-untyped-def]
    # the exact fall-through the step-2 review caught: ATTENTION must not read as OK
    state, gap, total, nongreen, residue = _seed_fleet_and_rollup(
        conn, workload="ATTENTION", workload_detail="queue depth", workload_as_of=_now())
    assert (state, nongreen) == ("ATTENTION", 1) and "test" in residue


def test_health_reach_lagging_replication_is_attention(conn):  # type: ignore[no-untyped-def]
    state, gap, total, nongreen, residue = _seed_fleet_and_rollup(
        conn, replication="LAGGING", replication_lag=7, replication_as_of=_now())
    assert (state, nongreen) == ("ATTENTION", 1) and "test" in residue


def test_refresh_refuses_autocommit_connections(conn):  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=True)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    try:
        with pytest.raises(ValueError, match="autocommit"):
            refresh_console_projections(c)
    finally:
        c.close()


def test_projection_state_registers_all_with_cursor(conn):  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    k.record_claim("cursor seed")
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT projection_name, state, last_event_id IS NOT NULL "
                    "FROM projection_state ORDER BY 1")
        assert cur.fetchall() == [("evidence_provenance", "current", True),
                                  ("fleet_node", "current", True),
                                  ("fleet_node_facet", "current", True),
                                  ("situation_rollup", "current", True)]


def test_refresh_is_idempotent_and_rebuild_includes_it(conn):  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    k.record_claim("rebuild seed")
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    refresh_console_projections(conn)                              # second run: same shape
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM situation_rollup")
        assert cur.fetchone()[0] == 5
    rebuild(conn)                                                  # full replay also refreshes
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM situation_rollup")
        assert cur.fetchone()[0] == 5
        cur.execute("SELECT count(*) FROM fleet_node")
        assert cur.fetchone()[0] == 1


def test_evidence_provenance_mirrors_links_one_to_one(conn):  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    a = k.record_claim("evidence source claim")
    b = k.record_claim("evidence target claim")
    k.assert_link(a.event_id, "supports", b.event_id)
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT source_ref, relation, target_ref, provenance, source_kind, "
                    "target_kind, actor_ref FROM evidence_provenance")
        rows = cur.fetchall()
        cur.execute("SELECT count(*) FROM event_link")
        assert cur.fetchone()[0] == len(rows)                     # 1:1, nothing added or dropped
    assert rows == [(a.event_id, "supports", b.event_id, "event_derived",
                     "claim.recorded", "claim.recorded", "pytest-console")]


def test_evidence_unheld_endpoint_stays_null_not_guessed(conn):  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    a = k.record_claim("claim grounded on something not held")
    k.assert_link(a.event_id, "based_on", "sha256:" + "0" * 64)   # target not in the local log
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT source_kind, target_kind FROM evidence_provenance")
        assert cur.fetchall() == [("claim.recorded", None)]        # NULL = not held, never guessed
