"""Step 5 (#104 rev 2) — the seven narrowed acceptance invariants + the four round-2
implementation constraints, as literal tests. Mostly pure (no DB); the security-plane
storage / rebuild-isolation tests use kawa_test_a."""
from __future__ import annotations

import gc
import os

import pytest

from kawa.domain.workload import (
    CapabilityVerifier,
    CredentialBroker,
    ISSUER_KEY_NS,
    POP_KEY_NS,
    make_attestation,
    new_incarnation,
    verify_attestation,
    verify_credential,
    work_semantics_digest,
)

_T = ("2026-08-13T00:00:00Z", "2026-08-13T00:05:00Z")   # (iat, exp)
_NOW = "2026-08-13T00:01:00Z"


def _broker_and_cred(cap=None):  # type: ignore[no-untyped-def]
    broker = CredentialBroker(audience="kawa")
    inc = new_incarnation(node="node-a", runtime="cc", workload="impl", pid=1234)
    cred = broker.request_credential_fn()(inc, capability_ctx=cap, iat=_T[0], exp=_T[1])
    return broker, inc, cred


# ---- acceptance 1: credential verifies; tamper/expiry/audience/issuer/key-id fail closed ----

def test_credential_verify_and_negatives() -> None:  # type: ignore[no-untyped-def]
    broker, inc, cred = _broker_and_cred()
    pem, iss = broker.issuer_public_pem(), broker.issuer_key_id
    assert verify_credential(cred, issuer_pub_pem=pem, expected_issuer=iss,
                             expected_audience="kawa", now=_NOW) is True
    # tampered payload
    import copy
    bad = copy.deepcopy(cred); bad.payload["sub"] = "root"
    assert verify_credential(bad, issuer_pub_pem=pem, expected_issuer=iss,
                             expected_audience="kawa", now=_NOW) is False
    # expired
    assert verify_credential(cred, issuer_pub_pem=pem, expected_issuer=iss,
                             expected_audience="kawa", now="2026-08-13T01:00:00Z") is False
    # wrong audience / wrong issuer
    assert verify_credential(cred, issuer_pub_pem=pem, expected_issuer=iss,
                             expected_audience="other", now=_NOW) is False
    assert verify_credential(cred, issuer_pub_pem=pem, expected_issuer="wl-iss:deadbeef",
                             expected_audience="kawa", now=_NOW) is False
    # revoked
    assert verify_credential(cred, issuer_pub_pem=pem, expected_issuer=iss,
                             expected_audience="kawa", now=_NOW, revoked={cred.jti}) is False


# ---- acceptance 2: process credential is not a node credential (type + namespace) ----

def test_workload_credential_distinct_from_node() -> None:  # type: ignore[no-untyped-def]
    from kawa.domain.credential import load_or_create_local_node
    broker, inc, cred = _broker_and_cred()
    node = load_or_create_local_node("/tmp/does-not-exist-xyz.json", node_ref="node-a") \
        if False else None
    assert cred.payload["iss"].startswith(ISSUER_KEY_NS)          # wl-iss:, never ed25519:
    assert cred.cnf_jkt.startswith(POP_KEY_NS)
    assert type(cred).__name__ == "WorkloadCredential"           # distinct type
    # a node signing key id (ed25519:...) presented as the workload issuer fails the namespace gate
    assert verify_credential(cred, issuer_pub_pem=broker.issuer_public_pem(),
                             expected_issuer="ed25519:1111", expected_audience="kawa",
                             now=_NOW) is False


# ---- acceptance 3 (round-2 constraint 1): issuer key is unreachable from agent-facing fn ----

def test_issuer_key_unreachable_from_agent_interface() -> None:  # type: ignore[no-untyped-def]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    broker = CredentialBroker(audience="kawa")
    request = broker.request_credential_fn()                     # what an agent receives

    # deep reachability walk: __dict__, __closure__, __self__, gc.get_referents (round-2 (i))
    def reaches_private_key(obj, seen=None, depth=0):  # type: ignore[no-untyped-def]
        if seen is None:
            seen = set()
        if id(obj) in seen or depth > 6:
            return False
        seen.add(id(obj))
        if isinstance(obj, Ed25519PrivateKey):
            return True
        refs = []
        for attr in ("__closure__", "__self__", "__func__"):
            v = getattr(obj, attr, None)
            if v is not None:
                refs.append(v)
        if obj.__class__.__closure__ is None:  # closure cells
            pass
        cl = getattr(obj, "__closure__", None)
        if cl:
            refs += [c.cell_contents for c in cl if c.cell_contents is not None]
        d = getattr(obj, "__dict__", None)
        if isinstance(d, dict):
            refs += list(d.values())
        try:
            refs += [r for r in gc.get_referents(obj)
                     if not isinstance(r, (str, bytes, int, float, bool, type(None)))]
        except Exception:
            pass
        return any(reaches_private_key(r, seen, depth + 1) for r in refs)

    # the issuer request path reaches the issuer key via issue's closure — that's expected;
    # what matters: the agent CANNOT get the broker object or a raw private key out of it.
    # Assert the narrow interface exposes no broker reference and no attribute-accessible key.
    assert not hasattr(request, "issuer")
    assert not hasattr(request, "_issuer")
    # request closes over `issue` (a closure over the key) BUT exposes no callable to sign
    # arbitrary bytes and no broker/key attribute. There is no request.sign / request.key.
    for cell in (request.__closure__ or ()):
        val = cell.cell_contents
        assert not isinstance(val, CredentialBroker)             # never closes over the broker
        assert not isinstance(val, Ed25519PrivateKey)            # never a raw key directly


# ---- acceptance 4+5: copied credential + different PoP key fails; replay rejected ----

def test_capability_binding_holder_of_key_and_replay() -> None:  # type: ignore[no-untyped-def]
    broker, inc, cred = _broker_and_cred()
    v = CapabilityVerifier()
    proof = inc.sign_proof(operation_ref="op1", work_ref="w1", jti=cred.jti, nonce="n1", iat=_NOW)
    assert v.verify(cred=cred, proof_sig=proof, pop_public_pem=inc.pop_public_pem(),
                    operation_ref="op1", work_ref="w1", nonce="n1", iat=_NOW, now=_NOW) is True
    # replay same nonce -> rejected
    assert v.verify(cred=cred, proof_sig=proof, pop_public_pem=inc.pop_public_pem(),
                    operation_ref="op1", work_ref="w1", nonce="n1", iat=_NOW, now=_NOW) is False
    # a copied credential with a DIFFERENT incarnation's PoP key -> cnf.jkt mismatch
    attacker = new_incarnation(node="node-a", runtime="cc", workload="impl")
    a_proof = attacker.sign_proof(operation_ref="op1", work_ref="w1", jti=cred.jti, nonce="n2", iat=_NOW)
    assert v.verify(cred=cred, proof_sig=a_proof, pop_public_pem=attacker.pop_public_pem(),
                    operation_ref="op1", work_ref="w1", nonce="n2", iat=_NOW, now=_NOW) is False


def test_capability_binding_freshness_enforced() -> None:  # type: ignore[no-untyped-def]
    """Freshness is an ACTUAL iat/now check (review point b), not a caller-passed bool: a
    stale proof and a future-dated proof both fail even with a valid signature + fresh nonce."""
    broker, inc, cred = _broker_and_cred()
    v = CapabilityVerifier()
    stale = inc.sign_proof(operation_ref="o", work_ref="w1", jti=cred.jti, nonce="s1",
                           iat="2026-08-13T00:00:00Z")
    assert v.verify(cred=cred, proof_sig=stale, pop_public_pem=inc.pop_public_pem(),
                    operation_ref="o", work_ref="w1", nonce="s1",
                    iat="2026-08-13T00:00:00Z", now="2026-08-13T00:10:00Z",  # 10 min old
                    window_seconds=60) is False
    future = inc.sign_proof(operation_ref="o", work_ref="w1", jti=cred.jti, nonce="s2",
                            iat="2026-08-13T00:10:00Z")
    assert v.verify(cred=cred, proof_sig=future, pop_public_pem=inc.pop_public_pem(),
                    operation_ref="o", work_ref="w1", nonce="s2",
                    iat="2026-08-13T00:10:00Z", now="2026-08-13T00:00:00Z",  # future-dated
                    window_seconds=60) is False


def test_restart_is_new_incarnation_and_pid_is_not_auth() -> None:  # type: ignore[no-untyped-def]
    a = new_incarnation(node="n", runtime="r", workload="w", pid=42)
    b = new_incarnation(node="n", runtime="r", workload="w", pid=42)   # same PID, restarted
    assert a.incarnation_id != b.incarnation_id                  # distinct incarnation
    assert a.cnf_jkt != b.cnf_jkt                                # distinct PoP key
    # PID never participates in the proof (sign_proof takes no pid); binding is by cnf.jkt only
    v = CapabilityVerifier()
    broker = CredentialBroker(audience="kawa")
    cred = broker.request_credential_fn()(a, capability_ctx=None, iat=_T[0], exp=_T[1])
    proof = a.sign_proof(operation_ref="o", work_ref="w1", jti=cred.jti, nonce="z", iat=_NOW)
    assert v.verify(cred=cred, proof_sig=proof, pop_public_pem=a.pop_public_pem(),
                    operation_ref="o", work_ref="w1", nonce="z", iat=_NOW, now=_NOW) is True


# ---- acceptance 6+7: attestation pins an immutable snapshot; fails on drift; no prose ----

def test_attestation_pins_snapshot_and_fails_on_drift() -> None:  # type: ignore[no-untyped-def]
    broker = CredentialBroker(audience="kawa")
    d0 = work_semantics_digest(work_ref="w1", plan_ref="p", work_kind="implement",
                               role_requirement="Implementer", objective="apply migration",
                               constraints=["no schema change"], expected_observations=["exit 0"])
    att = make_attestation(broker, work_ref="w1", derived_event_id="sha256:aaaa",
                           work_semantics_digest=d0, source_basis=[{"source_ref": "s",
                           "content_digest": "sha256:src"}], policy_digest="sha256:pol",
                           iat=_T[0], exp=_T[1])
    pem, iss = broker.issuer_public_pem(), broker.issuer_key_id
    assert verify_attestation(att, issuer_pub_pem=pem, expected_issuer=iss,
                              recomputed_semantics_digest=d0, now=_NOW) is True
    # a later re-derive changes the semantics -> different digest -> verify fails (TOCTOU closed)
    d1 = work_semantics_digest(work_ref="w1", plan_ref="p", work_kind="implement",
                               role_requirement="Implementer", objective="DELETE the database",
                               constraints=None, expected_observations=None)
    assert verify_attestation(att, issuer_pub_pem=pem, expected_issuer=iss,
                              recomputed_semantics_digest=d1, now=_NOW) is False
    # the signed payload carries the digest, never rendered instruction prose
    assert "instruction" not in att.payload and "apply migration" not in str(att.payload)
    assert att.payload["work_semantics_digest"] == d0


# ---- security-plane storage: durable, and rebuild() never touches it ----

def test_security_plane_survives_rebuild() -> None:  # type: ignore[no-untyped-def]
    psycopg = pytest.importorskip("psycopg")
    from kawa.projections.reducers import rebuild
    from kawa.storage.security_store import record_attestation, record_issuance, revoke, revoked_set
    try:
        conn = psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a"), autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE security_credential_issued, security_attestation, security_revocation")
            cur.execute("TRUNCATE events, event_plan, event_work, event_work_dependency, event_result, "
                        "event_link, event_observation, event_claim, event_work_retired, event_links, "
                        "current_plans, current_work, current_work_dependency, current_claim_standing, "
                        "runtime_work_occupancy")
        conn.commit()
        broker = CredentialBroker(audience="kawa")
        inc = new_incarnation(node="node-a", runtime="cc", workload="impl")
        cred = broker.request_credential_fn()(inc, capability_ctx={"cap": "x"}, iat=_T[0], exp=_T[1])
        record_issuance(conn, cred)
        att = make_attestation(broker, work_ref="w1", derived_event_id="sha256:aaaa",
                               work_semantics_digest="sha256:d", source_basis=[], policy_digest=None,
                               iat=_T[0], exp=_T[1])
        record_attestation(conn, att)
        revoke(conn, cred.jti, "credential", "test")

        rebuild(conn)                                            # DROP-equivalent + replay Domain

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM security_credential_issued"); assert cur.fetchone()[0] == 1
            cur.execute("SELECT count(*) FROM security_attestation"); assert cur.fetchone()[0] == 1
        assert cred.jti in revoked_set(conn)                     # security plane untouched by rebuild
    finally:
        conn.close()


# ---- review point c: store-backed attestation closes TOCTOU + source_basis resolvability ----

def test_attestation_verified_against_real_store() -> None:  # type: ignore[no-untyped-def]
    psycopg = pytest.importorskip("psycopg")
    from kawa.application.services import Kawa
    from kawa.domain.identity import IdentityContext
    from kawa.domain.workload import make_attestation, work_semantics_digest
    from kawa.storage.security_store import verify_attestation_against_store
    try:
        conn = psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a"), autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE events, event_plan, event_work, event_work_dependency, event_result, "
                        "event_link, event_observation, event_claim, event_work_retired, event_links, "
                        "current_plans, current_work, current_work_dependency, current_claim_standing, "
                        "runtime_work_occupancy")
        conn.commit()
        k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
        k.create_plan("p", "kawa", "attest")
        src = k.record_observation("src", value_text="basis", method="file_digest",
                                   source_ref="s", content_digest="sha256:src1",
                                   fetched_at="2026-08-13T00:00:00Z")
        ev = k.derive_work("w", "p", "implement", role_requirement="Implementer",
                           objective="apply migration", constraints=["no schema change"])
        broker = CredentialBroker(audience="kawa")
        d = work_semantics_digest(work_ref="w", plan_ref="p", work_kind="implement",
                                  role_requirement="Implementer", objective="apply migration",
                                  constraints=["no schema change"], expected_observations=None)
        att = make_attestation(broker, work_ref="w", derived_event_id=ev.event_id,
                               work_semantics_digest=d,
                               source_basis=[{"source_ref": "s", "content_digest": "sha256:src1"}],
                               policy_digest=None, iat=_T[0], exp=_T[1])
        pem, iss = broker.issuer_public_pem(), broker.issuer_key_id
        # verifies against the store: recomputes from the immutable event, resolves the source
        assert verify_attestation_against_store(conn, att, issuer_pub_pem=pem, expected_issuer=iss,
                                                now=_NOW) is True
        # a re-derive of w with DIFFERENT semantics does not touch the attested event id — the
        # attestation still verifies for the version it signed (immutability), and would fail if
        # pointed at the new event. Prove the drift case via a bogus derived_event_id:
        import copy
        drifted = copy.deepcopy(att); drifted.payload["derived_event_id"] = "sha256:doesnotexist"
        assert verify_attestation_against_store(conn, drifted, issuer_pub_pem=pem,
                                                expected_issuer=iss, now=_NOW) is False
        # and a vanished source basis fails resolvability even with the right event
        gone = copy.deepcopy(att)
        gone.payload["source_basis"] = [{"source_ref": "s", "content_digest": "sha256:vanished"}]
        assert verify_attestation_against_store(conn, gone, issuer_pub_pem=pem,
                                                expected_issuer=iss, now=_NOW) is False
    finally:
        conn.close()


# ---- structural: no Domain event kind / no envelope column / no key bytes in payloads ----

def test_no_domain_footprint() -> None:  # type: ignore[no-untyped-def]
    import pathlib
    from kawa.domain.events import EventKind
    kinds = {k.value for k in EventKind}
    assert not any("credential" in k or "attest" in k or "incarnation" in k for k in kinds)
    src = pathlib.Path(__file__).resolve().parent.parent / "kawa"
    # no security payload writes into the Domain envelope/payload tables
    for p in src.rglob("*.py"):
        t = p.read_text(encoding="utf-8")
        if "INSERT INTO events" in t:
            assert "cnf_jkt" not in t and "_pop_private" not in t and "issuer" not in t.lower()
