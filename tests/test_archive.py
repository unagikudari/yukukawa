"""Step 9c acceptance (#113 rev 2 (d)): segment commitments, layered tamper detection,
detached-outside-frontier semantics, restore-proof Observations, no pruning path.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.domain.trust import TrustRegistry
from kawa.storage.archive import (
    ArchiveVerificationError,
    archive_export,
    archive_import,
    verify_archive_file,
)
from kawa.storage.replication import frontier, pull

psycopg = pytest.importorskip("psycopg")

from tests.test_replication import _ALL  # noqa: E402

ATTESTED_AT = "2026-08-14T00:00:00Z"


def _fresh(dsn_env: str, default: str):  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get(dsn_env, default), autocommit=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    return c


@pytest.fixture()
def conn_a():  # type: ignore[no-untyped-def]
    c = _fresh("KAWA_TEST_DSN_A", "dbname=kawa_test_a")
    yield c
    c.close()


@pytest.fixture()
def conn_b():  # type: ignore[no-untyped-def]
    c = _fresh("KAWA_TEST_DSN_B", "dbname=kawa_test_b")
    yield c
    c.close()


@pytest.fixture()
def world(conn_a, tmp_path):  # type: ignore[no-untyped-def]
    """Node A with five fleet-scoped events, plus registries that know A's key."""
    cred = load_or_create_local_node(str(tmp_path / "a.json"), node_ref="node-a")
    kawa = Kawa(conn_a, identity=IdentityContext.from_local_node(cred, actor_ref="agent-a"))
    for i in range(1, 6):
        kawa.create_plan(f"p{i}", "kawa", f"event {i}")
    conn_a.commit()
    keys = PublicKeyRegistry(str(tmp_path / "keys.json"))
    trust = TrustRegistry(str(tmp_path / "trust.json"))
    keys.register(cred.signing_key_ref, cred.public_pem())
    trust.enroll("node-a", cred.signing_key_ref)
    return kawa, cred, keys, trust


def _segments(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT origin_node, from_seq, to_seq, detached FROM security_archive_segment "
                    "ORDER BY from_seq")
        return cur.fetchall()


def test_export_import_full_catchup(conn_a, conn_b, world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = world
    path = str(tmp_path / "seg-1-5.json")
    archive_export(conn_a, origin_node="node-a", from_seq=1, to_seq=5, path=path,
                   credential=cred, attested_at=ATTESTED_AT)
    report = archive_import(conn_b, path, keys=keys, trust=trust)
    assert len(report.admitted) == 5                      # genesis segment chains from empty
    assert frontier(conn_b) == {"node-a": 5}
    assert _segments(conn_b) == [("node-a", 1, 5, False)]  # chained custody evidence
    # export never mutated the source (§11 — read-only by construction)
    assert frontier(conn_a) == {"node-a": 5}


def test_tamper_layers_fail_separately(conn_a, conn_b, world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = world
    path = str(tmp_path / "seg.json")
    archive_export(conn_a, origin_node="node-a", from_seq=1, to_seq=5, path=path,
                   credential=cred, attested_at=ATTESTED_AT)
    doc = json.loads(open(path, encoding="utf-8").read())

    def _write(d, p):  # type: ignore[no-untyped-def]
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)

    # layer 1: forged archiver signature
    forged = dict(doc, signature="ab" * 32)
    _write(forged, str(tmp_path / "t1.json"))
    with pytest.raises(ArchiveVerificationError, match="signature"):
        verify_archive_file(str(tmp_path / "t1.json"), keys=keys)
    # layer 2: event-set tamper (drop one event, keep commitment)
    dropped = dict(doc, events=doc["events"][:-1])
    _write(dropped, str(tmp_path / "t2.json"))
    with pytest.raises(ArchiveVerificationError, match="event-set|boundary"):
        verify_archive_file(str(tmp_path / "t2.json"), keys=keys)
    # layer 3: boundary lie (commitment says the range ends earlier) — signature breaks first
    lied = json.loads(json.dumps(doc))
    lied["commitment"]["to_seq"] = 4
    _write(lied, str(tmp_path / "t3.json"))
    with pytest.raises(ArchiveVerificationError):
        verify_archive_file(str(tmp_path / "t3.json"), keys=keys)
    # layer 4: one byte of one event's payload
    byted = json.loads(json.dumps(doc))
    byted["events"][2]["payload_canonical"] = byted["events"][2]["payload_canonical"].replace(
        "event 3", "event x")
    _write(byted, str(tmp_path / "t4.json"))
    with pytest.raises(ArchiveVerificationError, match="byte"):
        verify_archive_file(str(tmp_path / "t4.json"), keys=keys)
    # and none of the tampered imports left ANY trace on B
    for name in ("t1", "t2", "t3", "t4"):
        with pytest.raises(ArchiveVerificationError):
            archive_import(conn_b, str(tmp_path / f"{name}.json"), keys=keys, trust=trust)
    assert frontier(conn_b) == {} and _segments(conn_b) == []


def test_detached_segment_stays_outside_frontier_then_gapfills(conn_a, conn_b, world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust = world
    tail = str(tmp_path / "seg-3-5.json")
    archive_export(conn_a, origin_node="node-a", from_seq=3, to_seq=5, path=tail,
                   credential=cred, attested_at=ATTESTED_AT)
    # importing the TAIL on an empty node: verified, recorded, DETACHED — no frontier entry,
    # no events admitted (normal admission refuses the gap), chain never weakened
    report = archive_import(conn_b, tail, keys=keys, trust=trust)
    assert report.admitted == []
    assert [x.reason for x in report.rejected] == ["chain_gap", "predecessor_rejected",
                                                   "predecessor_rejected"]
    assert frontier(conn_b) == {}
    assert _segments(conn_b) == [("node-a", 3, 5, True)]
    # the head arrives later (a plain pull); re-import now gap-fills through NORMAL admission
    head = str(tmp_path / "seg-1-2.json")
    archive_export(conn_a, origin_node="node-a", from_seq=1, to_seq=2, path=head,
                   credential=cred, attested_at=ATTESTED_AT)
    assert len(archive_import(conn_b, head, keys=keys, trust=trust).admitted) == 2
    report2 = archive_import(conn_b, tail, keys=keys, trust=trust)
    assert len(report2.admitted) == 3
    assert frontier(conn_b) == {"node-a": 5}
    assert _segments(conn_b) == [("node-a", 1, 2, False), ("node-a", 3, 5, False)]
    # regression: an IDEMPOTENT re-import (nothing newly admitted) must not flip a chained
    # segment's evidence back to detached — detached is a fact about the store
    report3 = archive_import(conn_b, tail, keys=keys, trust=trust)
    assert report3.admitted == []
    assert _segments(conn_b) == [("node-a", 1, 2, False), ("node-a", 3, 5, False)]


def test_restore_proof_recorded_including_failure(conn_a, world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from scripts.archive_verify import verify_and_record
    kawa, cred, keys, trust = world
    path = str(tmp_path / "seg.json")
    archive_export(conn_a, origin_node="node-a", from_seq=1, to_seq=5, path=path,
                   credential=cred, attested_at=ATTESTED_AT)
    assert verify_and_record(kawa, path, keys) is True
    # corrupt the file: the proof records FAILURE — never silence
    with open(path, "a", encoding="utf-8") as f:
        f.write(" ")
    doc = json.loads(open(path, encoding="utf-8").read())
    doc["signature"] = "ab" * 32
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    assert verify_and_record(kawa, path, keys) is False
    with conn_a.cursor() as cur:
        cur.execute("SELECT value_bool, content_digest IS NOT NULL, fetched_at IS NOT NULL "
                    "FROM event_observation WHERE predicate='archive_restore_ok' "
                    "ORDER BY event_id")
        rows = cur.fetchall()
    assert sorted(r[0] for r in rows) == [False, True]
    assert all(r[1] and r[2] for r in rows)               # source-binding tuple present


def test_stub_smuggling_rejected_on_import(conn_a, world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """#117 review hardening: the import-side mirror of the export stub guard — a file
    smuggling payload-less stubs under the bytes policy is refused before admission."""
    kawa, cred, keys, trust = world
    path = str(tmp_path / "seg.json")
    archive_export(conn_a, origin_node="node-a", from_seq=1, to_seq=5, path=path,
                   credential=cred, attested_at=ATTESTED_AT)
    doc = json.loads(open(path, encoding="utf-8").read())
    doc["events"][1]["payload_canonical"] = None          # stub-ify one event in place
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    with pytest.raises(ArchiveVerificationError, match="stub"):
        verify_archive_file(path, keys=keys)


def test_unreadable_archive_still_records_failure_proof(conn_a, world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """#117 review hardening: corruption below the JSON level records a FAILURE Observation —
    the 9c claim ('never silence') holds even for garbage bytes."""
    from scripts.archive_verify import verify_and_record
    kawa, cred, keys, trust = world
    path = str(tmp_path / "garbage.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json at all")
    assert verify_and_record(kawa, path, keys) is False
    with conn_a.cursor() as cur:
        cur.execute("SELECT value_bool, source_revision FROM event_observation "
                    "WHERE predicate='archive_restore_ok'")
        [(ok, note)] = cur.fetchall()
    assert ok is False and "unreadable archive" in note


def test_console_archive_screen_renders(conn_a, world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from kawa.console.render import render
    kawa, cred, keys, trust = world
    path = str(tmp_path / "seg.json")
    archive_export(conn_a, origin_node="node-a", from_seq=1, to_seq=5, path=path,
                   credential=cred, attested_at=ATTESTED_AT)
    archive_import(conn_a, path, keys=keys, trust=trust)   # self-import: evidence row (idempotent events)
    from scripts.archive_verify import verify_and_record
    verify_and_record(kawa, path, keys)
    page = render(conn_a, "/archive")
    assert page is not None and "Segment commitments" in page and "proof age" in page


def test_no_pruning_path_exists() -> None:
    """§11 structure test: the archive module can read and add — never remove."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "kawa" / "storage" / "archive.py"
           ).read_text(encoding="utf-8")
    for forbidden in ("DELETE FROM", "TRUNCATE", "DROP TABLE", "os.remove", "os.unlink"):
        assert forbidden not in src