"""Step 10 PR 3 acceptance (#118): the CP gates end-to-end — policy SoT + §6.1 fence,
sole-key catastrophic loss, genesis-conflict detection at admission. Real DBs.
"""
from __future__ import annotations

import json

import pytest

from kawa.application.services import Kawa
from kawa.domain.authority import (
    FORK_RESOLUTION_KEY,
    POLICY_KEY,
    configuration_coordinate_digest,
    fork_resolution_operation_digest,
    make_proof,
    policy_operation_digest,
)
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.events import AuthorityConfiguration, AuthorityReceipt
from kawa.domain.identity import IdentityContext
from kawa.domain.ids import canonical_json, digest
from kawa.domain.trust import TrustRegistry
from kawa.storage.authority_gate import (
    AuthorityRefused,
    capability_policy_from_lineage,
    current_policy_digest,
    establish_policy,
    resolve_fork,
)
from kawa.storage.replication import pull

psycopg = pytest.importorskip("psycopg")

from tests.test_archive import conn_a, conn_b, _fresh  # noqa: E402,F401


@pytest.fixture()
def op_world(conn_a, tmp_path):  # type: ignore[no-untyped-def]
    """One node with its operator credential, registries, and a genesis Cell per key."""
    cred = load_or_create_local_node(str(tmp_path / "op.json"), node_ref="node-a")
    kawa = Kawa(conn_a, identity=IdentityContext.from_local_node(cred, actor_ref="op"),
                default_scope=None)
    keys = PublicKeyRegistry(str(tmp_path / "keys.json"))
    trust = TrustRegistry(str(tmp_path / "trust.json"))
    keys.register(cred.signing_key_ref, cred.public_pem())
    trust.enroll("node-a", cred.signing_key_ref)

    cells = {}
    for akey in (FORK_RESOLUTION_KEY, POLICY_KEY):
        cd = configuration_coordinate_digest(authority_key=akey, authority_epoch=0,
                                             members=[cred.signing_key_ref], quorum=1,
                                             prior_configuration_digest=None)
        kawa._emit_reduce(AuthorityConfiguration(
            authority_key=akey, configuration_digest=cd, authority_epoch=0,
            members=[cred.signing_key_ref], quorum=1, succession_proof=make_proof(cd, [cred])))
        cells[akey] = cd
    conn_a.commit()

    def receipt(akey, op_digest, policy_digest=None):  # type: ignore[no-untyped-def]
        ev = kawa._emit_reduce(AuthorityReceipt(
            authority_key=akey, operation_digest=op_digest, configuration_digest=cells[akey],
            authority_epoch=0, policy_digest=policy_digest,
            quorum_proof=make_proof(op_digest, [cred])))
        conn_a.commit()
        return ev.event_id

    return kawa, cred, keys, trust, cells, receipt


def test_policy_sot_establish_supersede_and_capability_source(conn_a, op_world) -> None:  # type: ignore[no-untyped-def]
    kawa, cred, keys, trust, cells, receipt = op_world
    assert current_policy_digest(conn_a) is None
    assert capability_policy_from_lineage(conn_a) is None      # no policy: fail closed
    bundle_a = canonical_json({"authorized": ["deploy", "review"]})
    op = policy_operation_digest(op="policy.establish", policy_digest=digest(bundle_a),
                                 prior_policy_digest=None,
                                 configuration_digest=cells[POLICY_KEY])
    pa = establish_policy(conn_a, trust, canonical_bundle=bundle_a,
                          receipt_event_id=receipt(POLICY_KEY, op), keys=keys)
    assert current_policy_digest(conn_a) == pa == digest(bundle_a)
    assert capability_policy_from_lineage(conn_a).authorized == frozenset({"deploy", "review"})
    # supersession: receipt binds the PRIOR digest — lineage is explicit, never a swap
    bundle_b = canonical_json({"authorized": ["deploy"]})
    op2 = policy_operation_digest(op="policy.supersede", policy_digest=digest(bundle_b),
                                  prior_policy_digest=pa,
                                  configuration_digest=cells[POLICY_KEY])
    pb = establish_policy(conn_a, trust, canonical_bundle=bundle_b,
                          receipt_event_id=receipt(POLICY_KEY, op2, policy_digest=pa),
                          keys=keys)
    assert current_policy_digest(conn_a) == pb
    assert capability_policy_from_lineage(conn_a).authorized == frozenset({"deploy"})
    # a receipt for the WRONG lineage step does not exercise
    op3 = policy_operation_digest(op="policy.supersede", policy_digest=digest(bundle_a),
                                  prior_policy_digest=pa,        # stale prior: head is pb now
                                  configuration_digest=cells[POLICY_KEY])
    with pytest.raises(AuthorityRefused):
        establish_policy(conn_a, trust, canonical_bundle=bundle_a,
                         receipt_event_id=receipt(POLICY_KEY, op3, policy_digest=pb), keys=keys)


def test_policy_fence_rule_a_rejects_stale_commits(conn_a, conn_b, op_world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§6.1 rule (a): a fork-resolution receipt authorized under policy A must not commit
    after A was superseded by B — re-initiate under B, never a silent stale commit."""
    kawa, cred, keys, trust, cells, receipt = op_world
    # freeze an origin: a rival at a held position from another credential's stream
    other = load_or_create_local_node(str(tmp_path / "other.json"), node_ref="node-x")
    keys.register(other.signing_key_ref, other.public_pem())
    trust.enroll("node-x", other.signing_key_ref)
    kx = Kawa(conn_b, identity=IdentityContext.from_local_node(other, actor_ref="x"),
              default_scope=None)
    kx.create_plan("px", "kawa", "their trunk")
    conn_b.commit()
    pull(conn_a, conn_b, keys=keys, trust=trust)
    from tests.test_incarnation import _rival
    rival = _rival("node-x", 1, None, other)
    from kawa.storage.replication import admit_batch
    admit_batch(conn_a, [rival], keys=keys, trust=trust)
    with conn_a.cursor() as cur:
        cur.execute("SELECT held_event_id FROM security_fork_evidence WHERE origin_node='node-x'")
        held = cur.fetchone()[0]
    # policy A is in force; the receipt pins it
    bundle_a = canonical_json({"authorized": ["resolve"]})
    op_est = policy_operation_digest(op="policy.establish", policy_digest=digest(bundle_a),
                                     prior_policy_digest=None,
                                     configuration_digest=cells[POLICY_KEY])
    pa = establish_policy(conn_a, trust, canonical_bundle=bundle_a,
                          receipt_event_id=receipt(POLICY_KEY, op_est), keys=keys)
    op_fork = fork_resolution_operation_digest(origin_node="node-x", origin_seq=1,
                                               chosen_head=held,
                                               configuration_digest=cells[FORK_RESOLUTION_KEY],
                                               policy_digest=pa)
    stale_receipt = receipt(FORK_RESOLUTION_KEY, op_fork, policy_digest=pa)
    # …but policy B lands before the commit
    bundle_b = canonical_json({"authorized": []})
    op_sup = policy_operation_digest(op="policy.supersede", policy_digest=digest(bundle_b),
                                     prior_policy_digest=pa,
                                     configuration_digest=cells[POLICY_KEY])
    establish_policy(conn_a, trust, canonical_bundle=bundle_b,
                     receipt_event_id=receipt(POLICY_KEY, op_sup, policy_digest=pa), keys=keys)
    with pytest.raises(AuthorityRefused, match="policy_superseded"):
        resolve_fork(conn_a, trust, origin_node="node-x", origin_seq=1, chosen_head=held,
                     receipt_event_id=stale_receipt, keys=keys)
    with conn_a.cursor() as cur:                                # still frozen: fail-closed
        cur.execute("SELECT frozen FROM security_fork_evidence WHERE origin_node='node-x'")
        assert cur.fetchone()[0] is True


def test_sole_key_loss_blocks_forever_no_fallback(conn_a, conn_b, op_world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """r1 (c): losing the 1-member Cell's key is catastrophic quorum loss — resolve_fork
    refuses forever (fresh connections included); nothing falls back to operator authority."""
    kawa, cred, keys, trust, cells, receipt = op_world
    other = load_or_create_local_node(str(tmp_path / "other.json"), node_ref="node-x")
    keys.register(other.signing_key_ref, other.public_pem())
    trust.enroll("node-x", other.signing_key_ref)
    kx = Kawa(conn_b, identity=IdentityContext.from_local_node(other, actor_ref="x"),
              default_scope=None)
    kx.create_plan("px", "kawa", "their trunk")
    conn_b.commit()
    pull(conn_a, conn_b, keys=keys, trust=trust)
    from tests.test_incarnation import _rival
    from kawa.storage.replication import admit_batch
    admit_batch(conn_a, [_rival("node-x", 1, None, other)], keys=keys, trust=trust)
    with conn_a.cursor() as cur:
        cur.execute("SELECT held_event_id FROM security_fork_evidence WHERE origin_node='node-x'")
        held = cur.fetchone()[0]
    op = fork_resolution_operation_digest(origin_node="node-x", origin_seq=1, chosen_head=held,
                                          configuration_digest=cells[FORK_RESOLUTION_KEY],
                                          policy_digest=None)
    r = receipt(FORK_RESOLUTION_KEY, op)
    trust.revoke(cred.signing_key_ref)                          # the sole member's key is gone
    with pytest.raises(AuthorityRefused, match="receipt_invalid"):
        resolve_fork(conn_a, trust, origin_node="node-x", origin_seq=1, chosen_head=held,
                     receipt_event_id=r, keys=keys)
    # a "restart" (fresh connection, fresh gate state) changes nothing — and no other
    # code path can unfreeze (the operator path does not exist: grepped in test below)
    import os
    with psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a")) as conn_a2:
        with pytest.raises(AuthorityRefused, match="receipt_invalid"):
            resolve_fork(conn_a2, trust, origin_node="node-x", origin_seq=1, chosen_head=held,
                         receipt_event_id=r, keys=keys)
    with conn_a.cursor() as cur:
        cur.execute("SELECT frozen FROM security_fork_evidence WHERE origin_node='node-x'")
        assert cur.fetchone()[0] is True


def test_bare_operator_path_is_gone() -> None:
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "kawa" / "storage" /
           "replication.py").read_text(encoding="utf-8")
    assert "operator_ref" not in src                            # the fallback does not exist
    gate = (pathlib.Path(__file__).resolve().parents[1] / "kawa" / "storage" /
            "authority_gate.py").read_text(encoding="utf-8")
    assert "operator_ref" not in gate


def test_genesis_conflict_detected_at_admission(conn_a, conn_b, op_world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """rev 2 (b): two deployments independently mint facially-valid genesis for one key
    over non-shared state; replication converges every node to the SAME durable conflict
    evidence, both lines blocked — and an UNPROVEN genesis is reported noise, not a rival."""
    kawa, cred, keys, trust, cells, receipt = op_world
    other = load_or_create_local_node(str(tmp_path / "other.json"), node_ref="node-x")
    keys.register(other.signing_key_ref, other.public_pem())
    trust.enroll("node-x", other.signing_key_ref)
    kx = Kawa(conn_b, identity=IdentityContext.from_local_node(other, actor_ref="x"),
              default_scope=None)
    # an UNPROVEN genesis first: reported, admitted, never a rival
    fake_cd = configuration_coordinate_digest(authority_key=FORK_RESOLUTION_KEY,
                                              authority_epoch=0,
                                              members=[other.signing_key_ref], quorum=1,
                                              prior_configuration_digest=None)
    kx._emit_reduce(AuthorityConfiguration(
        authority_key=FORK_RESOLUTION_KEY, configuration_digest=fake_cd, authority_epoch=0,
        members=[other.signing_key_ref], quorum=1, succession_proof=None))
    conn_b.commit()
    r1 = pull(conn_a, conn_b, keys=keys, trust=trust)
    assert "authority_genesis_unproven" in {x.reason for x in r1.rejected}
    with conn_a.cursor() as cur:
        cur.execute("SELECT count(*) FROM security_authority_conflict")
        assert cur.fetchone()[0] == 0                           # noise, not a conflict
    from kawa.storage.authority_gate import load_proof_store
    from kawa.domain.authority import current_configuration
    standing, head = current_configuration(load_proof_store(conn_a), FORK_RESOLUTION_KEY, keys)
    assert standing == "VALID" and head.configuration_digest == cells[FORK_RESOLUTION_KEY]
    # now a FACIALLY-VALID rival genesis: the administrative conflict, recorded, both block
    rival_cd = configuration_coordinate_digest(authority_key=FORK_RESOLUTION_KEY,
                                               authority_epoch=0,
                                               members=[other.signing_key_ref], quorum=1,
                                               prior_configuration_digest=None)
    # (same coordinate as fake_cd — but THIS one is properly signed; the store keys by
    #  digest so the proven one must differ: vary via a two-member set)
    rival_cd = configuration_coordinate_digest(
        authority_key=FORK_RESOLUTION_KEY, authority_epoch=0,
        members=[other.signing_key_ref, cred.signing_key_ref], quorum=2,
        prior_configuration_digest=None)
    kx._emit_reduce(AuthorityConfiguration(
        authority_key=FORK_RESOLUTION_KEY, configuration_digest=rival_cd, authority_epoch=0,
        members=[other.signing_key_ref, cred.signing_key_ref], quorum=2,
        succession_proof=make_proof(rival_cd, [other, cred])))
    conn_b.commit()
    r2 = pull(conn_a, conn_b, keys=keys, trust=trust)
    assert "authority_genesis_conflict" in {x.reason for x in r2.rejected}
    with conn_a.cursor() as cur:
        cur.execute("SELECT authority_key FROM security_authority_conflict")
        assert cur.fetchone()[0] == FORK_RESOLUTION_KEY         # durable, named
    standing, _ = current_configuration(load_proof_store(conn_a), FORK_RESOLUTION_KEY, keys)
    assert standing == "INVALID"                                # both lines blocked, no last-wins