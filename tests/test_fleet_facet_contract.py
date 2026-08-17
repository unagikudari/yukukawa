"""Fleet telemetry checkpoint 1 (#189 rev 3): the frozen registry (§A), the
facet DDL (§C), and the failure-honesty rules (§B) as literal predicates —
BEFORE any collector or reducer code exists."""
from __future__ import annotations

import os

import pytest

from kawa.domain.events import ObservationMethod
from kawa.projections.facet_registry import REGISTRY, Facet

psycopg = pytest.importorskip("psycopg")

_NOW = "2026-08-17T12:00:00Z"
_ROW = ("INSERT INTO fleet_node_facet (node_ref, predicate, qualifier, {col}, unit, "
        "occurred_at, fetched_at, source_ref, source_event_id) "
        "VALUES ('nod_example_a', %s, %s, %s, %s, %s, %s, 'proc://loadavg', 'sha256:x')")


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute("TRUNCATE fleet_node_facet")
    c.commit()
    yield c
    c.rollback()
    c.close()


def _insert(conn, predicate, qualifier, col, value, unit=None):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute(_ROW.format(col=col), (predicate, qualifier, value, unit, _NOW, _NOW))


# ---- §A: the registry is the frozen mirror of the plan table ----

def test_registry_mirrors_the_frozen_plan_table():  # type: ignore[no-untyped-def]
    assert len(REGISTRY) == 10                                     # exactly the ten §A rows
    assert REGISTRY["node_reachable"] == Facet("value_bool", None, "label", "http_probe")
    assert REGISTRY["node_disk_free_bytes"].qualifier_kind == "mount"
    assert REGISTRY["node_gpu_vram_used_bytes"] == Facet("value_number", "bytes", "gpu", "command_exit")
    for name, f in REGISTRY.items():
        assert name.startswith("node_")                            # flat family convention
        assert f.value_column in ("value_number", "value_text", "value_bool")
        assert f.qualifier_kind in ("none", "mount", "gpu", "label")


def test_registry_methods_are_valid_observation_methods():  # type: ignore[no-untyped-def]
    valid = set(ObservationMethod.__args__)  # type: ignore[attr-defined]
    assert {f.method for f in REGISTRY.values()} <= valid


# ---- §C: exactly one value, bool first-class ----

def test_exactly_one_value_is_schema_enforced(conn):  # type: ignore[no-untyped-def]
    _insert(conn, "node_cpu_load1", "", "value_number", 1.5, "load")     # one value: fine
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fleet_node_facet (node_ref, predicate, qualifier, "
                        "value_number, value_text, occurred_at, fetched_at, source_ref, "
                        "source_event_id) VALUES ('n','p','',1,'x',%s,%s,'s','e')", (_NOW, _NOW))
    conn.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):                   # zero values: refused
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fleet_node_facet (node_ref, predicate, qualifier, "
                        "occurred_at, fetched_at, source_ref, source_event_id) "
                        "VALUES ('n','p','',%s,%s,'s','e')", (_NOW, _NOW))
    conn.rollback()


def test_bool_is_first_class(conn):  # type: ignore[no-untyped-def]
    _insert(conn, "node_reachable", "console", "value_bool", True)
    with conn.cursor() as cur:
        cur.execute("SELECT value_bool, value_number, value_text FROM fleet_node_facet")
        assert cur.fetchall() == [(True, None, None)]


# ---- §C: qualifier sentinel — deterministic PK, no NULL semantics ----

def test_qualifier_sentinel_makes_the_key_deterministic(conn):  # type: ignore[no-untyped-def]
    _insert(conn, "node_cpu_load1", "", "value_number", 1.0, "load")
    with pytest.raises(psycopg.errors.UniqueViolation):                  # '' collides with ''
        _insert(conn, "node_cpu_load1", "", "value_number", 2.0, "load")
    conn.rollback()
    _insert(conn, "node_disk_free_bytes", "/", "value_number", 100.0, "bytes")
    _insert(conn, "node_disk_free_bytes", "/home", "value_number", 200.0, "bytes")
    with conn.cursor() as cur:                                           # qualifiers separate keys
        cur.execute("SELECT count(*) FROM fleet_node_facet WHERE predicate='node_disk_free_bytes'")
        assert cur.fetchone()[0] == 2


def test_null_qualifier_is_impossible(conn):  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.NotNullViolation):
        with conn.cursor() as cur:
            cur.execute("INSERT INTO fleet_node_facet (node_ref, predicate, qualifier, "
                        "value_number, occurred_at, fetched_at, source_ref, source_event_id) "
                        "VALUES ('n','p',NULL,1,%s,%s,'s','e')", (_NOW, _NOW))
    conn.rollback()


# ---- §C vocabulary: occurred_at, and time/source binding is not optional ----

def test_vocabulary_is_occurred_at_with_mandatory_binding(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='fleet_node_facet'")
        cols = {r[0] for r in cur.fetchall()}
    assert "occurred_at" in cols and "observed_at" not in cols           # one vocabulary
    for missing in ("occurred_at", "fetched_at", "source_ref", "source_event_id"):
        with pytest.raises(psycopg.errors.NotNullViolation):
            with conn.cursor() as cur:
                keep = {c: f"'{_NOW}'" if "at" in c else "'x'"
                        for c in ("occurred_at", "fetched_at", "source_ref", "source_event_id")
                        if c != missing}
                cur.execute("INSERT INTO fleet_node_facet (node_ref, predicate, qualifier, "
                            "value_number, " + ", ".join(keep) + ") VALUES ('n','p','',1, "
                            + ", ".join(keep.values()) + ")")
        conn.rollback()


# ---- §B (schema side): absence is row absence — no defaultable value exists ----

def test_no_value_column_has_a_default(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT column_name, column_default FROM information_schema.columns "
                    "WHERE table_name='fleet_node_facet' AND column_name LIKE 'value_%%'")
        assert all(default is None for _, default in cur.fetchall())     # zeros cannot appear by default
