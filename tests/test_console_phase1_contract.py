"""Console phase-1 contract (#181 rev 3, step 1): the seven cross-screen
invariants + the epistemic ground rules as LITERAL predicates over the DDL
(sql/0021_console_phase1_projections.sql) and fixtures. NO reducer or screen
code exists yet — these tests pin the contract those will be built against."""
from __future__ import annotations

import os

import pytest

psycopg = pytest.importorskip("psycopg")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NOW = "2026-08-17T12:00:00Z"


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute("TRUNCATE situation_rollup, fleet_node, evidence_provenance")
    c.commit()
    yield c
    c.rollback()
    c.close()


def _rejected(conn, sql, params=()):  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.cursor() as cur:
            cur.execute(sql, params)
    conn.rollback()


# ---- invariant 1: five dimensions, never merged ----

def test_dimension_enum_admits_exactly_the_five(conn):  # type: ignore[no-untyped-def]
    for d in ("AUTHORITY", "EXECUTION", "EPISTEMIC", "HEALTH_REACH", "PROJECTION_FRESHNESS"):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO situation_rollup (dimension, state, source_projection, as_of) "
                        "VALUES (%s,'UNKNOWN','fixture',%s)", (d, _NOW))
    _rejected(conn, "INSERT INTO situation_rollup (dimension, state, source_projection, as_of) "
                    "VALUES ('OVERALL','OK','fixture',%s)", (_NOW,))


def test_no_merged_health_column_exists(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name IN ('situation_rollup','fleet_node')")
        cols = {r[0] for r in cur.fetchall()}
    assert not {c for c in cols if "overall" in c or "combined" in c or c == "health"}


# ---- invariant 3 + ground rule: INCOMPLETE is first-class and names its gap ----

def test_incomplete_requires_named_gap(conn):  # type: ignore[no-untyped-def]
    _rejected(conn, "INSERT INTO situation_rollup (dimension, state, source_projection, as_of) "
                    "VALUES ('AUTHORITY','INCOMPLETE','fixture',%s)", (_NOW,))
    with conn.cursor() as cur:  # with the gap named, it is a valid first-class state
        cur.execute("INSERT INTO situation_rollup (dimension, state, gap, source_projection, as_of) "
                    "VALUES ('AUTHORITY','INCOMPLETE','verifier read-model absent (phase 2+)','fixture',%s)",
                    (_NOW,))


def test_counts_never_travel_without_their_residue(conn):  # type: ignore[no-untyped-def]
    _rejected(conn, "INSERT INTO situation_rollup (dimension, state, count_total, count_nongreen, "
                    "source_projection, as_of) VALUES ('EXECUTION','ATTENTION',5,2,'fixture',%s)", (_NOW,))


def test_gap_belongs_to_incomplete_only(conn):  # type: ignore[no-untyped-def]
    # UNKNOWN is absence-of-evidence; giving it a named gap would blur it into
    # INCOMPLETE (checkpoint-review fix) — the schema rejects the blur
    _rejected(conn, "INSERT INTO situation_rollup (dimension, state, gap, source_projection, as_of) "
                    "VALUES ('PROJECTION_FRESHNESS','UNKNOWN','no projection reports yet','fixture',%s)",
              (_NOW,))
    _rejected(conn, "INSERT INTO situation_rollup (dimension, state, gap, source_projection, as_of) "
                    "VALUES ('AUTHORITY','INCOMPLETE','','fixture',%s)", (_NOW,))   # empty gap = no gap


def test_count_coherence_is_schema_enforced(conn):  # type: ignore[no-untyped-def]
    _rejected(conn, "INSERT INTO situation_rollup (dimension, state, count_total, count_nongreen, "
                    "residue, source_projection, as_of) VALUES ('EXECUTION','ATTENTION',2,10,'x','fixture',%s)",
              (_NOW,))                                     # nongreen > total
    _rejected(conn, "INSERT INTO situation_rollup (dimension, state, count_total, "
                    "source_projection, as_of) VALUES ('EXECUTION','OK',-1,'fixture',%s)", (_NOW,))
    _rejected(conn, "INSERT INTO situation_rollup (dimension, state, count_total, count_nongreen, "
                    "residue, source_projection, as_of) VALUES ('EXECUTION','ATTENTION',5,2,'','fixture',%s)",
              (_NOW,))                                     # empty residue = no residue


# ---- invariant 2: standing axes verbatim (guards additions) ----

def test_standing_axis_names_verbatim_or_absent(conn):  # type: ignore[no-untyped-def]
    verbatim = {"authority_standing", "effect_standing", "consumption_standing",
                "epistemic_standing"}
    with conn.cursor() as cur:
        cur.execute("SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_name IN ('situation_rollup','fleet_node','evidence_provenance') "
                    "AND column_name LIKE '%%standing%%'")
        found = {c for _, c in cur.fetchall()}
    assert found <= verbatim                              # phase 1: empty; additions must be verbatim


# ---- invariants 6 + 8: STALE unstorable; absence /= negative evidence ----

def test_stale_is_render_derived_never_stored(conn):  # type: ignore[no-untyped-def]
    for col in ("reachability", "workload", "replication"):
        _rejected(conn, f"INSERT INTO fleet_node (node_ref, {col}) VALUES ('nod_example_a','STALE')")


def test_unknown_and_measured_negative_are_distinct_states(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        # absence of evidence: UNKNOWN, no source, no as_of
        cur.execute("INSERT INTO fleet_node (node_ref) VALUES ('nod_example_a')")
        # measured negative: CRIT/LAGGING with named source + freshness
        cur.execute("INSERT INTO fleet_node (node_ref, reachability, reachability_source, "
                    "reachability_as_of, replication, replication_lag, replication_as_of) "
                    "VALUES ('nod_example_b','CRIT','observation:node_probe',%s,'LAGGING',42,%s)",
                    (_NOW, _NOW))
        cur.execute("SELECT node_ref, reachability, replication FROM fleet_node ORDER BY 1")
        rows = cur.fetchall()
    assert rows == [("nod_example_a", "UNKNOWN", "UNKNOWN"),
                    ("nod_example_b", "CRIT", "LAGGING")]


def test_measured_state_cannot_be_invented_without_named_source(conn):  # type: ignore[no-untyped-def]
    # reachability CRIT with no named source event = invented value -> rejected
    _rejected(conn, "INSERT INTO fleet_node (node_ref, reachability) VALUES ('nod_example_c','CRIT')")
    # UNKNOWN with a source named makes no sense either (absence claims no source)
    _rejected(conn, "INSERT INTO fleet_node (node_ref, reachability_source) "
                    "VALUES ('nod_example_d','observation:node_probe')")


def test_unknown_replication_cannot_carry_a_lag_number(conn):  # type: ignore[no-untyped-def]
    _rejected(conn, "INSERT INTO fleet_node (node_ref, replication_lag) VALUES ('nod_example_e',7)")


def test_lag_travels_with_lagging_exactly(conn):  # type: ignore[no-untyped-def]
    # CURRENT may not carry a lag; LAGGING must carry a positive one (review fix)
    _rejected(conn, "INSERT INTO fleet_node (node_ref, replication, replication_lag, replication_as_of) "
                    "VALUES ('nod_example_f','CURRENT',999999,%s)", (_NOW,))
    _rejected(conn, "INSERT INTO fleet_node (node_ref, replication, replication_as_of) "
                    "VALUES ('nod_example_g','LAGGING',%s)", (_NOW,))
    _rejected(conn, "INSERT INTO fleet_node (node_ref, replication, replication_lag, replication_as_of) "
                    "VALUES ('nod_example_h','LAGGING',0,%s)", (_NOW,))


# ---- invariant 7 (fleet side): a written cell state requires its freshness ----

def test_written_cell_state_requires_its_as_of(conn):  # type: ignore[no-untyped-def]
    _rejected(conn, "INSERT INTO fleet_node (node_ref, reachability, reachability_source) "
                    "VALUES ('nod_example_i','CRIT','observation:node_probe')")
    _rejected(conn, "INSERT INTO fleet_node (node_ref, workload, workload_as_of) "
                    "VALUES ('nod_example_j','OK',NULL)")
    _rejected(conn, "INSERT INTO fleet_node (node_ref, replication, replication_lag) "
                    "VALUES ('nod_example_k','LAGGING',5)")


# ---- invariant 4: provenance distinction; missing renders as missing ----

def test_provenance_admits_only_event_derived(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("INSERT INTO evidence_provenance (link_event_id, source_ref, relation, "
                    "target_ref, recorded_at, actor_ref) "
                    "VALUES ('sha256:fixture1','ev:a','based_on','ev:b',%s,'fixture')", (_NOW,))
    _rejected(conn, "INSERT INTO evidence_provenance (link_event_id, source_ref, relation, "
                    "target_ref, provenance, recorded_at, actor_ref) "
                    "VALUES ('sha256:fixture2','ev:a','based_on','ev:c','inferred',%s,'fixture')", (_NOW,))


def test_missing_provenance_is_row_absence(conn):  # type: ignore[no-untyped-def]
    # non-empty table, queried ref has no rows: absence of THIS chain is
    # distinguishable from an empty projection (checkpoint-review fix — the
    # earlier form was vacuously true on an empty table)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO evidence_provenance (link_event_id, source_ref, relation, "
                    "target_ref, recorded_at, actor_ref) "
                    "VALUES ('sha256:fixture-present','ev:a','based_on','ev:b',%s,'fixture')", (_NOW,))
        cur.execute("SELECT count(*) FROM evidence_provenance")
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM evidence_provenance WHERE source_ref='ev:nothing' "
                    "OR target_ref='ev:nothing'")
        assert cur.fetchone()[0] == 0        # the honest render input for "missing provenance"


def test_untyped_relation_is_rejected(conn):  # type: ignore[no-untyped-def]
    _rejected(conn, "INSERT INTO evidence_provenance (link_event_id, source_ref, relation, "
                    "target_ref, recorded_at, actor_ref) "
                    "VALUES ('sha256:fixture3','ev:a','vibes','ev:b',%s,'fixture')", (_NOW,))


# ---- invariant 5: growth /= authority ----

def test_no_skill_growth_surface_exists(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('skill_growth')")
        assert cur.fetchone()[0] is None
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name IN ('situation_rollup','fleet_node','evidence_provenance') "
                    "AND (column_name LIKE '%%skill%%' OR column_name LIKE '%%growth%%')")
        assert cur.fetchall() == []


# ---- invariant 5 (fleet side): no attestation cell in phase 1 ----

def test_fleet_carries_no_attestation_cell(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='fleet_node' AND column_name LIKE '%%attest%%'")
        assert cur.fetchall() == []


# ---- invariant 7: freshness always surfaced ----

def test_rollup_freshness_is_not_optional(conn):  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.NotNullViolation):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO situation_rollup (dimension, state, source_projection) "
                        "VALUES ('EXECUTION','OK','fixture')")
    conn.rollback()
