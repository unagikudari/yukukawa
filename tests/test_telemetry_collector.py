"""Step-2 telemetry tests (#189 rev 3): collector parse/failure rules (§B) and
the registry-gated, latest-wins facet reducer (§C).

Fixture provenance: the df and nvidia-smi fixtures are REAL captured outputs
from a live node (2026-08-17) — fixtures come from real measurements."""
from __future__ import annotations

import os
import sys

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.projections.console_rollup import refresh_console_projections

psycopg = pytest.importorskip("psycopg")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

import collect_node_telemetry as ct  # noqa: E402

# real `df -B1 --output=target,size,avail` capture (one junk line appended to
# exercise the per-qualifier skip path — §B multi-target partial failure)
_DF_REAL = (b"Mounted on     1B-blocks        Avail\n"
            b"/          1005867986944 459113373696\n"
            b"/boot         1020702720    724746240\n"
            b"garbage line without numbers\n")
# real nvidia-smi capture from the live node
_NVIDIA_REAL = b"0, NVIDIA GeForce RTX 5070 Ti, 16303, 6264\n"

_TABLES = ("content_embedding, event_content, events, event_links, event_link, "
           "event_observation, event_claim, event_plan, event_work, "
           "event_work_dependency, event_work_retired, event_result, "
           "current_claim_standing, current_plans, current_work, "
           "current_work_dependency, runtime_work_occupancy, work_dispatch, "
           "situation_rollup, fleet_node, evidence_provenance, projection_state, "
           "fleet_node_facet")


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


# ---- §B parse rules on real fixtures ----

def test_df_parse_is_per_qualifier_with_named_skips():  # type: ignore[no-untyped-def]
    readings, skipped = ct.parse_df(_DF_REAL)
    mounts = {r.qualifier for r in readings}
    assert mounts == {"/", "/boot"}                       # readable targets emit
    assert len(readings) == 4                             # total+free per mount
    assert skipped == ["garbage line without numbers"]    # the skip is NAMED, not silent
    by = {(r.predicate, r.qualifier): r.value for r in readings}
    assert by[("node_disk_free_bytes", "/")] == 459113373696.0   # real measured value


def test_nvidia_parse_yields_model_and_vram():  # type: ignore[no-untyped-def]
    readings = ct._parse_nvidia(_NVIDIA_REAL)
    by = {(r.predicate, r.qualifier): r.value for r in readings}
    assert by[("node_gpu_model", "0")] == "NVIDIA GeForce RTX 5070 Ti"
    assert by[("node_gpu_vram_total_bytes", "0")] == 16303 * 1024 * 1024
    assert by[("node_gpu_vram_used_bytes", "0")] == 6264 * 1024 * 1024


def test_unparseable_gpu_output_is_family_failure_not_zeros():  # type: ignore[no-untyped-def]
    assert ct._parse_nvidia(b"driver error\n") is None    # None -> caller raises (§B), no zeros


def test_every_reading_predicate_is_registry_gated():  # type: ignore[no-untyped-def]
    with pytest.raises(AssertionError):
        ct.Reading("node_totally_invented", "", 1.0, "x", b"")


# ---- §C reducer: registry gating + latest-wins on the frozen ordering ----

def _emit(k, predicate, value_number, qualifier="", occurred="2026-08-17T10:00:00Z",
          fetched=None):  # type: ignore[no-untyped-def]
    return k.record_observation(
        predicate, value_number=value_number, method="metric_read",
        occurred_at=occurred, source_ref="file:///proc/test",
        source_revision=f"tool=test qualifier={qualifier}",
        content_digest="sha256:0", fetched_at=fetched or occurred)


def test_reducer_projects_only_registry_predicates_latest_wins(conn):  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-telemetry"))
    _emit(k, "node_cpu_load1", 1.0, occurred="2026-08-17T10:00:00Z")
    _emit(k, "node_cpu_load1", 2.0, occurred="2026-08-17T11:00:00Z")   # later wins
    _emit(k, "node_disk_free_bytes", 5.0, qualifier="/")
    _emit(k, "node_disk_free_bytes", 7.0, qualifier="/home")           # distinct keys
    k.record_observation("unregistered_metric", value_number=9.0, method="metric_read",
                         occurred_at="2026-08-17T10:00:00Z", source_ref="x",
                         source_revision="tool=test qualifier=",
                         content_digest="sha256:0", fetched_at="2026-08-17T10:00:00Z")
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT predicate, qualifier, value_number, unit FROM fleet_node_facet "
                    "ORDER BY predicate, qualifier")
        rows = cur.fetchall()
    assert rows == [("node_cpu_load1", "", 2.0, "load"),
                    ("node_disk_free_bytes", "/", 5.0, "bytes"),
                    ("node_disk_free_bytes", "/home", 7.0, "bytes")]
    # the unregistered predicate exists in the LOG (observations are free to
    # record it) but never becomes a facet — the registry is the gate


def test_space_containing_mount_qualifier_survives(conn):  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-telemetry"))
    _emit(k, "node_disk_free_bytes", 42.0, qualifier="/mnt/backup drive")
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT qualifier FROM fleet_node_facet WHERE predicate='node_disk_free_bytes'")
        assert cur.fetchall() == [("/mnt/backup drive",)]     # spaces intact, no truncation


def test_fetched_at_breaks_occurred_ties(conn):  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-telemetry"))
    same = "2026-08-17T10:00:00Z"
    _emit(k, "node_cpu_load1", 1.0, occurred=same, fetched="2026-08-17T10:00:01Z")
    _emit(k, "node_cpu_load1", 3.0, occurred=same, fetched="2026-08-17T10:00:05Z")
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT value_number FROM fleet_node_facet WHERE predicate='node_cpu_load1'")
        assert cur.fetchone()[0] == 3.0                    # §C: fetched_at is tie-break #2


def test_fleet_screen_renders_facet_line_with_age(conn):  # type: ignore[no-untyped-def]
    from kawa.console.render import render
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-telemetry"))
    _emit(k, "node_cpu_load1", 0.42)
    _emit(k, "node_mem_total_bytes", float(32 << 30))
    _emit(k, "node_mem_available_bytes", float(18 << 30))
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    page = render(conn, "/fleet")
    assert "cpu 0.42" in page and "mem 18.0GiB free/32.0GiB" in page
    assert "ago" in page                                   # age surfaced (§B staleness-as-age)
    assert "no telemetry collector on this node" not in page.split('"wr">test<')[1][:600]


# real df capture shape with a space-containing mount appended (the mount
# string itself is synthetic-on-real-format: df pads columns identically)
_DF_SPACE = (b"Mounted on     1B-blocks        Avail\n"
             b"/          1005867986944 459113373696\n"
             b"/mnt/USB Drive    64000000000  32000000000\n")


def test_df_parse_survives_space_containing_mounts():  # type: ignore[no-untyped-def]
    readings, skipped = ct.parse_df(_DF_SPACE)
    assert skipped == []
    by = {(r.predicate, r.qualifier): r.value for r in readings}
    assert by[("node_disk_free_bytes", "/mnt/USB Drive")] == 32000000000.0


def test_subsecond_timestamps_do_not_lose_to_lexicographic_order(conn):  # type: ignore[no-untyped-def]
    # review finding (c): text compare ranks '...00Z' ABOVE '...00.500Z'
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref="test", actor_ref="pytest-telemetry"))
    _emit(k, "node_cpu_load1", 1.0, occurred="2026-08-17T10:00:00Z")
    _emit(k, "node_cpu_load1", 9.0, occurred="2026-08-17T10:00:00.500Z")   # newer, subsecond
    conn.commit()
    refresh_console_projections(conn)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT value_number FROM fleet_node_facet WHERE predicate='node_cpu_load1'")
        assert cur.fetchone()[0] == 9.0                    # timestamptz cast, not ASCII


def test_gpu_failing_first_tool_still_tries_the_next(monkeypatch):  # type: ignore[no-untyped-def]
    import subprocess as sp
    calls = []

    def fake_run(cmd, capture_output=True, timeout=20):  # type: ignore[no-untyped-def]
        calls.append(cmd[0])
        class P:  # noqa: N801
            returncode = 6 if cmd[0] == "nvidia-smi" else 0
            stdout = b"" if cmd[0] == "nvidia-smi" else b'{"card0": {"VRAM Total Memory (B)": 17163091968, "VRAM Total Used Memory (B)": 1000000}}'
            stderr = b"NVML: Driver/library version mismatch" if cmd[0] == "nvidia-smi" else b""
        return P()
    monkeypatch.setattr(sp, "run", fake_run)
    readings = ct.read_gpu()
    assert calls == ["nvidia-smi", "rocm-smi"]             # fallback happened (review (b))
    by = {(r.predicate, r.qualifier): r.value for r in readings}
    assert by[("node_gpu_vram_total_bytes", "0")] == 17163091968.0


def test_no_gpu_tool_anywhere_is_absence(monkeypatch):  # type: ignore[no-untyped-def]
    # REAL fleet measurement (2026-08-17, AMD node): rocm-smi does not exist
    # there at all — 'command not found' — its VRAM lives in amdgpu sysfs.
    # Absence of every tool is ABSENCE (None), never zeros, never an error.
    import subprocess as sp

    def fake_run(cmd, capture_output=True, timeout=20):  # type: ignore[no-untyped-def]
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(sp, "run", fake_run)
    assert ct.read_gpu() is None
