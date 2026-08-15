"""Step 12A — fail-closed connect, the test fence negative controls, and the
archive cycle invariants (#129 rev 3 F1/F2 + R2/R6/R8), as literal tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.storage.db import default_dsn

psycopg = pytest.importorskip("psycopg")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FENCED_DOGFOOD = "dbname=kawa user=kawa_test password=kawa_test host=127.0.0.1"


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute("TRUNCATE content_embedding, event_content, events, event_links, "
                    "event_link, event_observation, event_claim, event_plan, event_work, "
                    "event_work_dependency, event_work_retired, event_result, "
                    "current_claim_standing, current_plans, current_work, "
                    "current_work_dependency, runtime_work_occupancy, work_dispatch")
    c.commit()
    yield c
    c.close()


# ---- F1: fail-closed connect ----

def test_connect_refuses_to_guess_a_database(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("KAWA_DSN", raising=False)
    with pytest.raises(RuntimeError, match="fail-closed"):
        default_dsn()


def test_pytest_runs_under_the_fenced_credential():  # type: ignore[no-untyped-def]
    """The conftest layer really did pin the fenced role for THIS run."""
    assert "user=kawa_test" in os.environ["KAWA_TEST_DSN_A"]
    assert "KAWA_DSN" not in os.environ


# ---- F1: the negative control, with the REAL credential ----

def test_fenced_credential_cannot_reach_dogfood():  # type: ignore[no-untyped-def]
    """The credential pytest actually uses must be REFUSED by the dogfood DB.
    Success here (a connection) means the fence is down — that is a failure."""
    try:
        c = psycopg.connect(_FENCED_DOGFOOD, connect_timeout=3)
    except psycopg.OperationalError:
        return                                     # refused: fence holds
    c.close()
    pytest.fail("fenced role CONNECTED to the dogfood database — fence is down")


def test_migration_entrypoint_is_fenced():  # type: ignore[no-untyped-def]
    """The actual automation path (apply_migrations) pointed at dogfood under
    the fenced credential must fail — not silently run."""
    proc = subprocess.run(
        [sys.executable, os.path.join(_REPO, "scripts", "apply_migrations.py")],
        env={**os.environ, "KAWA_DSN": _FENCED_DOGFOOD},
        capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0


# ---- archive cycle (R2 loud path, R6 isolation, R8 policy digest) ----

def _seed(k, n):  # type: ignore[no-untyped-def]
    for i in range(n):
        k.record_claim(f"durability seed claim {i}")


def _cycle(conn, tmp_path, size=3):  # type: ignore[no-untyped-def]
    sys.path.insert(0, os.path.join(_REPO, "scripts"))
    from archive_cycle import run_cycle
    return run_cycle(
        conn, node_ref="test", actor_ref="pytest-archive",
        archive_dir=str(tmp_path / "segments"), segment_size=size,
        status_file=str(tmp_path / "status" / "archive.status"),
        credential_path=str(tmp_path / "node_credential.json"),
        keys_path=str(tmp_path / "keys.json"))


def test_cycle_exports_proves_and_records(conn, tmp_path):  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    _seed(k, 7)
    conn.commit()
    status = _cycle(conn, tmp_path)
    assert status["ok"] and status["exported_this_run"] == 2      # 7 events, size 3
    assert status["lag"]["test"] == 1                             # tail stated, not silent
    assert status["policy_digest"].startswith("sha256:")
    with conn.cursor() as cur:
        cur.execute("SELECT o.value_bool, o.source_revision FROM event_observation o "
                    "WHERE o.predicate='archive_restore_proof'")
        rows = cur.fetchall()
    assert len(rows) == 1 and rows[0][0] is True
    assert status["policy_digest"] in rows[0][1]                  # R8: digest embedded
    saved = json.load(open(tmp_path / "status" / "archive.status"))
    assert saved["ok"] is True                                    # local-first surface


def test_cycle_is_idempotent(conn, tmp_path):  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    _seed(k, 6)
    conn.commit()
    first = _cycle(conn, tmp_path)
    second = _cycle(conn, tmp_path)
    assert first["exported_this_run"] == 2 and second["exported_this_run"] == 0
    assert second["ok"]


def test_corrupted_segment_fails_loudly(conn, tmp_path):  # type: ignore[no-untyped-def]
    """R2: a damaged archive yields value_bool=false + error class + ok=false —
    never silence. (The drill isolation rule R6: this runs in tmp_path only.)"""
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    _seed(k, 4)
    conn.commit()
    _cycle(conn, tmp_path)
    seg_dir = tmp_path / "segments"
    victim = sorted(seg_dir.iterdir())[0]
    doc = json.loads(victim.read_text())
    doc["events"][0]["payload_canonical"] += " "     # one tampered byte
    victim.write_text(json.dumps(doc))
    status = _cycle(conn, tmp_path)
    assert status["ok"] is False and status["failed"] == 1
    with conn.cursor() as cur:
        cur.execute("SELECT o.value_bool, o.source_revision FROM event_observation o "
                    "JOIN events e ON e.event_id = o.event_id "
                    "WHERE o.predicate='archive_restore_proof' ORDER BY e.origin_seq")
        rows = cur.fetchall()
    assert rows[-1][0] is False
    assert "error_class=archive_verification" in rows[-1][1]


def test_layer_negatives_signature_setdigest_boundary(conn, tmp_path):  # type: ignore[no-untyped-def]
    """PR #130 review: explicit negative controls for layers 1-3 + the
    malformed-file normalization (never an unhandled crash)."""
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    _seed(k, 3)
    conn.commit()
    _cycle(conn, tmp_path)
    seg = sorted((tmp_path / "segments").iterdir())[0]
    pristine = seg.read_text()

    def expect_failure(mutate):  # type: ignore[no-untyped-def]
        doc = json.loads(pristine)
        mutate(doc)
        seg.write_text(json.dumps(doc))
        status = _cycle(conn, tmp_path)
        assert status["ok"] is False and status["failed"] == 1

    expect_failure(lambda d: d.update(signature="ab" * 32))                 # layer 1
    expect_failure(                                                         # layer 2 (via the
        lambda d: d["commitment"].update(event_set_digest="sha256:wrong"))  # commitment digest)
    expect_failure(lambda d: d["events"].pop())                             # layer 3 boundary
    expect_failure(lambda d: d.pop("events"))                               # malformed: KeyError path
    seg.write_text(pristine)
    assert _cycle(conn, tmp_path)["ok"] is True                             # restored => green
