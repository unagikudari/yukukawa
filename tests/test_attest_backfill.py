"""Step 12B — custodian attestation backfill + sign-at-birth enforcement
(#129 deviation review findings 1–3).

The unsigned fixture is the 2026-08-15 production measurement in miniature
(unsigned history, admission rejects everything); the backfill test proves the
actual unlock end-to-end: sign → enroll → the same pull that rejected now admits.
"""
from __future__ import annotations

import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.domain.trust import TrustRegistry
from kawa.storage.replication import frontier, pull
from scripts.attest_backfill import backfill, ephemeral_credential

psycopg = pytest.importorskip("psycopg")

from tests.test_replica_pull import _fresh  # same fenced-DB fixture discipline


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


def _unsigned_history(conn, origin: str, n_extra: int = 0):  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(
        node_ref=origin, actor_ref="pre-12b"))
    k.create_plan("p1", "kawa", "unsigned history")
    k.derive_work("w1", "p1", "implement")
    k.record_result("w1", "success", "r1")
    conn.commit()


def test_backfill_signs_audits_and_unlocks_replication(conn_a, conn_b, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The 12B unlock, end-to-end: unsigned history → audited backfill → the pull that
    rejected everything now admits everything. Idempotent on rerun."""
    _unsigned_history(conn_a, "node-a")
    signer = load_or_create_local_node(str(tmp_path / "node-a.json"), node_ref="node-a")
    keys = PublicKeyRegistry(str(tmp_path / "keys.json"))
    audit_identity = IdentityContext.from_local_node(signer, actor_ref="attest-backfill")

    summary = backfill(conn_a, origin="node-a", signer=signer, keys=keys,
                       audit_identity=audit_identity)
    conn_a.commit()
    assert summary["signed"] == 3 and summary["key_ref"] == signer.signing_key_ref

    with conn_a.cursor() as cur:                   # every event signed, none skipped
        cur.execute("SELECT count(*) FROM events WHERE origin_node='node-a' "
                    "AND signature IS NULL")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT value_number, source_revision FROM event_observation "
                    "WHERE predicate='attestation_backfill'")
        rows = cur.fetchall()
    assert len(rows) == 1 and rows[0][0] == 3.0    # the audit trail (finding 3)
    assert f"key_ref={signer.signing_key_ref}" in rows[0][1]
    assert "seqs=1..3" in rows[0][1]

    trust = TrustRegistry(str(tmp_path / "trust.json"))
    trust.enroll("node-a", signer.signing_key_ref)
    report = pull(conn_b, conn_a, keys=keys, trust=trust)
    assert len(report.admitted) == 4 and report.rejected == []   # 3 + the audit event
    assert frontier(conn_b)["node-a"] == 4

    again = backfill(conn_a, origin="node-a", signer=signer, keys=keys,
                     audit_identity=audit_identity)
    assert again["signed"] == 0                    # idempotent — nothing re-signed


def test_ephemeral_key_and_receiver_seal_close_a_terminated_origin(conn_a, conn_b, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Finding 2: a terminated origin ('test'/'local' class) is signed with a key that
    no longer exists, and the receiver's from_seq seal rejects anything past the head."""
    _unsigned_history(conn_a, "legacy")
    signer = ephemeral_credential("legacy")
    keys = PublicKeyRegistry(str(tmp_path / "keys.json"))
    auditor = load_or_create_local_node(str(tmp_path / "custodian.json"), node_ref="custodian")
    keys.register(auditor.signing_key_ref, auditor.public_pem())

    summary = backfill(conn_a, origin="legacy", signer=signer, keys=keys,
                       audit_identity=IdentityContext.from_local_node(
                           auditor, actor_ref="attest-backfill"))
    conn_a.commit()
    head = summary["head"]

    trust = TrustRegistry(str(tmp_path / "trust.json"))
    trust.enroll("legacy", signer.signing_key_ref)
    trust.enroll("custodian", auditor.signing_key_ref)
    trust.revoke(signer.signing_key_ref, from_seq=head + 1)      # the seal
    report = pull(conn_b, conn_a, keys=keys, trust=trust)
    assert len(report.admitted) == head + 1        # legacy 1..head + the audit event
    assert report.rejected == []

    # a post-seal continuation signed by the same key is REJECTED at admission
    from kawa.domain.events import ClaimRecorded
    from kawa.storage.emit import Emitter
    Emitter(conn_a, identity=IdentityContext.from_local_node(
        signer, actor_ref="attacker")).emit(ClaimRecorded(proposition="rogue", basis_note=None))
    conn_a.commit()
    late = pull(conn_b, conn_a, keys=keys, trust=trust)
    assert late.admitted == []
    assert any(r.reason == "trust_revoked" for r in late.rejected)


def test_trigger_permits_only_the_monotone_backfill(conn_a) -> None:  # type: ignore[no-untyped-def]
    """sql/0015: signature NULL→value (all three columns together) is the ONLY new
    transition; re-signing and every other mutation stay forbidden."""
    _unsigned_history(conn_a, "node-a")
    signer = ephemeral_credential("node-a")

    with conn_a.cursor() as cur:                   # partial provenance → forbidden
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("UPDATE events SET signature='ab' WHERE origin_node='node-a' "
                        "AND origin_seq=1")
    conn_a.rollback()

    with conn_a.cursor() as cur:                   # unrelated column → forbidden
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("UPDATE events SET actor_ref='rewritten' WHERE origin_node='node-a' "
                        "AND origin_seq=1")
    conn_a.rollback()

    with conn_a.cursor() as cur:                   # the permitted monotone transition
        cur.execute("SELECT self_hash FROM events WHERE origin_node='node-a' AND origin_seq=1")
        sh = cur.fetchone()[0]
        cur.execute("UPDATE events SET signature=%s, signing_key_ref=%s, signature_scheme=%s "
                    "WHERE origin_node='node-a' AND origin_seq=1",
                    (signer.sign(sh), signer.signing_key_ref, signer.signature_scheme))
        with pytest.raises(psycopg.errors.RaiseException):   # re-signing → forbidden
            cur.execute("UPDATE events SET signature='cd', signing_key_ref='k', "
                        "signature_scheme='ed25519' "
                        "WHERE origin_node='node-a' AND origin_seq=1")
    conn_a.rollback()


def test_emit_refuses_unattested_against_live_target(conn_a, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Finding 1 mechanized: naming a live target (KAWA_DSN) forbids unsigned emission.
    The test fence composes — tests run with KAWA_DSN dropped, so this must be explicit."""
    monkeypatch.setenv("KAWA_DSN", "dbname=kawa")
    k = Kawa(conn_a, identity=IdentityContext.from_local_runtime(
        node_ref="node-a", actor_ref="drifter"))
    with pytest.raises(RuntimeError, match="unattested emit against a live target"):
        k.create_plan("p-live", "kawa", "must refuse")
    conn_a.rollback()

    monkeypatch.delenv("KAWA_DSN")
    k2 = Kawa(conn_a, identity=IdentityContext.from_local_runtime(
        node_ref="node-a", actor_ref="test-only"))
    k2.create_plan("p-test", "kawa", "fine without a live target")
    conn_a.commit()
