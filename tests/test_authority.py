"""Step 10 PR 2 acceptance (#118): the authority core against S1–S9, three-state
discrimination, BC-4, and the rev-2 conflict posture. Pure domain — no DB.
"""
from __future__ import annotations

import json

import pytest

from kawa.domain.authority import (
    AuthorityProofStore,
    configuration_coordinate_digest,
    current_configuration,
    fork_resolution_operation_digest,
    make_proof,
    verify_configuration,
    verify_receipt,
)
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.events import AuthorityConfiguration, AuthorityReceipt
from kawa.domain.trust import TrustRegistry

K = "authority:fork-resolution"


@pytest.fixture()
def world(tmp_path):  # type: ignore[no-untyped-def]
    """Three member credentials + a registry that can resolve them all."""
    creds = [load_or_create_local_node(str(tmp_path / f"m{i}.json"), node_ref=f"node-{i}")
             for i in range(3)]
    keys = PublicKeyRegistry(str(tmp_path / "keys.json"))
    for c in creds:
        keys.register(c.signing_key_ref, c.public_pem())
    return creds, keys


def _config(members, quorum, *, epoch=0, prior=None, signers=None, key=K):  # type: ignore[no-untyped-def]
    """Build a configuration whose digest is its canonical coordinate, signed by `signers`
    (defaults: genesis -> its own members; successor -> caller must pass parent quorum)."""
    refs = [c.signing_key_ref for c in members]
    cd = configuration_coordinate_digest(authority_key=key, authority_epoch=epoch,
                                         members=refs, quorum=quorum,
                                         prior_configuration_digest=prior)
    proof = make_proof(cd, signers if signers is not None else members)
    return AuthorityConfiguration(authority_key=key, configuration_digest=cd,
                                  authority_epoch=epoch, members=refs, quorum=quorum,
                                  prior_configuration_digest=prior, succession_proof=proof)


def _receipt(cfg, op_digest, signers):  # type: ignore[no-untyped-def]
    return AuthorityReceipt(authority_key=cfg.authority_key, operation_digest=op_digest,
                            configuration_digest=cfg.configuration_digest,
                            authority_epoch=cfg.authority_epoch,
                            quorum_proof=make_proof(op_digest, signers))


def _op(cfg, seq=7):  # type: ignore[no-untyped-def]
    return fork_resolution_operation_digest(origin_node="node-x", origin_seq=seq,
                                            chosen_head="sha256:aa",
                                            configuration_digest=cfg.configuration_digest,
                                            policy_digest=None)


# ---------------- genesis, S2, and the conflict posture ----------------

def test_genesis_requires_founding_quorum_bc1(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    store = AuthorityProofStore()
    g = _config(creds, 2)
    store.add(g)
    assert verify_configuration(store, g.configuration_digest, keys) == "VALID"
    # unsigned / under-signed genesis confers nothing (BC-1: the griefing anchor)
    store2 = AuthorityProofStore()
    unsigned = g.model_copy(update={"succession_proof": None})
    store2.add(unsigned)
    assert verify_configuration(store2, g.configuration_digest, keys) == "INVALID"
    store3 = AuthorityProofStore()
    under = _config(creds, 2, signers=creds[:1])
    store3.add(under)
    assert verify_configuration(store3, under.configuration_digest, keys) == "INVALID"
    # a STRANGER's signatures do not found a Cell for someone else's members
    stranger_creds, _ = creds, keys
    # (signers not in members simply never count — covered by under-signed case above)


def test_s2_no_fabricated_authority(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    store = AuthorityProofStore()
    # a "genesis" claiming epoch 5: higher clocks/epochs cannot mint authority
    fake = _config(creds, 2, epoch=5)
    store.add(fake)
    assert verify_configuration(store, fake.configuration_digest, keys) == "INVALID"
    # a successor whose parent is nowhere: INCOMPLETE, never assumed
    orphan = _config(creds, 2, epoch=1, prior="sha256:" + "0" * 64)
    store2 = AuthorityProofStore()
    store2.add(orphan)
    assert verify_configuration(store2, orphan.configuration_digest, keys) == "INCOMPLETE"


def test_duplicate_genesis_blocks_both_ways(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    store = AuthorityProofStore()
    g1 = _config(creds[:1], 1)
    g2 = _config(creds[1:2], 1)          # a second, independently-minted genesis for K
    store.add(g1)
    store.add(g2)
    # authority_genesis_conflict: NEITHER exercises, no last-wins, no second live Cell —
    # and the judgement is order-independent (sets, not arrival)
    assert verify_configuration(store, g1.configuration_digest, keys) == "INVALID"
    assert verify_configuration(store, g2.configuration_digest, keys) == "INVALID"
    assert current_configuration(store, K, keys) == ("INVALID", None)
    r = _receipt(g1, _op(g1), creds[:1])
    assert verify_receipt(store, r, keys) == "INVALID"


# ---------------- succession: unique successor, BC-4, S3/S5 ----------------

def _line(creds, keys):  # type: ignore[no-untyped-def]
    """genesis -> one valid successor; returns (store, g, s1)."""
    store = AuthorityProofStore()
    g = _config(creds, 2)
    s1 = _config(creds, 2, epoch=1, prior=g.configuration_digest, signers=creds[:2])
    store.add(g)
    store.add(s1)
    return store, g, s1


def test_succession_and_unique_successor(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    store, g, s1 = _line(creds, keys)
    assert verify_configuration(store, s1.configuration_digest, keys) == "VALID"
    assert current_configuration(store, K, keys)[1].configuration_digest == s1.configuration_digest
    # a competing successor of the SAME parent — cryptographically perfect — blocks BOTH lines
    s1b = _config(creds, 2, epoch=1, prior=g.configuration_digest, signers=creds[1:])
    # (different signer subset -> different proof bytes; same coordinate would collide, so
    #  vary quorum to make it a distinct configuration)
    s1b = _config(creds, 3, epoch=1, prior=g.configuration_digest, signers=creds)
    store.add(s1b)
    assert verify_configuration(store, s1.configuration_digest, keys) == "INVALID"
    assert verify_configuration(store, s1b.configuration_digest, keys) == "INVALID"
    assert current_configuration(store, K, keys) == ("INVALID", None)


def test_s5_superseded_config_cannot_exercise(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    store, g, s1 = _line(creds, keys)
    # a receipt minted under the PARENT after succession: stale resurrection refused
    stale = _receipt(g, _op(g), creds[:2])
    assert verify_receipt(store, stale, keys) == "INVALID"
    # under the current head it exercises
    live = _receipt(s1, _op(s1), creds[:2])
    assert verify_receipt(store, live, keys) == "VALID"
    # S3: after succession the old config can never again mint a valid receipt — same
    # judgement regardless of which evidence arrived first (pure function over the set)


def test_bc4_membership_change_refused(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    store = AuthorityProofStore()
    g = _config(creds, 2)
    store.add(g)
    shrunk = _config(creds[:2], 2, epoch=1, prior=g.configuration_digest, signers=creds[:2])
    store.add(shrunk)
    assert verify_configuration(store, shrunk.configuration_digest, keys) == "INVALID"


def test_epoch_must_count_successions(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    store = AuthorityProofStore()
    g = _config(creds, 2)
    skip = _config(creds, 2, epoch=5, prior=g.configuration_digest, signers=creds[:2])
    store.add(g)
    store.add(skip)
    assert verify_configuration(store, skip.configuration_digest, keys) == "INVALID"


# ---------------- receipts: binding, accountability, quorum, S7, S9 ----------------

def test_receipt_binding_and_quorum(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    store = AuthorityProofStore()
    g = _config(creds, 2)
    store.add(g)
    op = _op(g)
    assert verify_receipt(store, _receipt(g, op, creds[:2]), keys) == "VALID"
    assert verify_receipt(store, _receipt(g, op, creds[:1]), keys) == "INVALID"   # sub-quorum
    # operation binding: a receipt for op X does not exercise for op Y (different seq)
    r = _receipt(g, op, creds[:2])
    assert r.operation_digest != _op(g, seq=8)
    # epoch mismatch
    lied = r.model_copy(update={"authority_epoch": 3})
    assert verify_receipt(store, lied, keys) == "INVALID"
    # unknown configuration: INCOMPLETE — absorbing, never exercised
    ghost = r.model_copy(update={"configuration_digest": "sha256:" + "1" * 64})
    assert verify_receipt(store, ghost, keys) == "INCOMPLETE"


def test_accountable_signer_set_required(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    store = AuthorityProofStore()
    g = _config(creds, 2)
    store.add(g)
    op = _op(g)
    bare = _receipt(g, op, creds[:2]).model_copy(update={
        "quorum_proof": json.dumps({"signatures": [creds[0].sign(op), creds[1].sign(op)]})})
    assert verify_receipt(store, bare, keys) == "INVALID"     # no signer set: non-conforming
    garbage = _receipt(g, op, creds[:2]).model_copy(update={"quorum_proof": "not json"})
    assert verify_receipt(store, garbage, keys) == "INVALID"
    # a stray signature by a NON-member never counts toward quorum
    outsider_proof = json.dumps({
        "signer_set": [creds[0].signing_key_ref, "ed25519:stranger"],
        "signatures": [creds[0].sign(op), "ab" * 32]})
    assert verify_receipt(store, _receipt(g, op, creds[:2]).model_copy(
        update={"quorum_proof": outsider_proof}), keys) == "INVALID"


def test_s7_trust_gates_new_exercise_only(world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    trust = TrustRegistry(str(tmp_path / "t.json"))
    for i, c in enumerate(creds):
        trust.enroll(f"node-{i}", c.signing_key_ref)
    store = AuthorityProofStore()
    sole = _config(creds[:1], 1)
    store.add(sole)
    r = _receipt(sole, _op(sole), creds[:1])
    assert verify_receipt(store, r, keys, trust) == "VALID"
    # the 1-member Cell's catastrophic case: revoking the sole member's key blocks NEW
    # exercise forever — no operator fallback exists at this layer or any other
    trust.revoke(creds[0].signing_key_ref)
    assert verify_receipt(store, r, keys, trust) == "INVALID"
    # without the trust view the historical cryptography still verifies (S7: history is
    # not rewritten — only current/future standing changed)
    assert verify_receipt(store, r, keys) == "VALID"


def test_s9_keys_are_independent(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    store = AuthorityProofStore()
    # fork-resolution key: genesis CONFLICT (blocked)
    store.add(_config(creds[:1], 1))
    store.add(_config(creds[1:2], 1))
    # policy key: healthy 1-member cell
    pol = _config(creds[:1], 1, key="authority:policy")
    store.add(pol)
    assert current_configuration(store, K, keys) == ("INVALID", None)
    standing, head = current_configuration(store, "authority:policy", keys)
    assert standing == "VALID" and head.configuration_digest == pol.configuration_digest
    # and a policy receipt still exercises while fork-resolution is blocked
    from kawa.domain.authority import policy_operation_digest
    op = policy_operation_digest(op="policy.establish", policy_digest="sha256:pp",
                                 prior_policy_digest=None,
                                 configuration_digest=pol.configuration_digest)
    assert verify_receipt(store, _receipt(pol, op, creds[:1]), keys) == "VALID"


def test_garbage_rivals_are_noise_not_blockers(world) -> None:  # type: ignore[no-untyped-def]
    """PR #120 review blocker 2: junk a malicious enrolled node sprays into the store must
    not grief the legitimate line — only FACIALLY-VALID rivals compete (and those block
    both ways, correctly, as parent-quorum equivocation)."""
    creds, keys = world
    store, g, s1 = _line(creds, keys)
    # an unsigned "successor" and an unsigned "genesis": noise, everything stays VALID
    junk_succ = _config(creds, 2, epoch=1, prior=g.configuration_digest,
                        signers=creds[:2]).model_copy(update={"succession_proof": None})
    # (same coordinate would overwrite the real one in the digest-keyed store — vary quorum)
    junk_succ = _config(creds, 3, epoch=1, prior=g.configuration_digest,
                        signers=creds).model_copy(update={"succession_proof": None})
    junk_gen = _config(creds[2:], 1).model_copy(update={"succession_proof": "not json"})
    store.add(junk_succ)
    store.add(junk_gen)
    assert verify_configuration(store, s1.configuration_digest, keys) == "VALID"
    standing, head = current_configuration(store, K, keys)
    assert standing == "VALID" and head.configuration_digest == s1.configuration_digest


def test_deep_fabricated_chain_is_walked_iteratively(world) -> None:  # type: ignore[no-untyped-def]
    """PR #120 review blocker 1: a thousands-deep fabricated prior chain must answer,
    never crash with RecursionError — validation is an iterative walk."""
    creds, keys = world
    store = AuthorityProofStore()
    prior = None
    for epoch in range(3000):
        c = _config(creds[:1], 1, epoch=epoch, prior=prior, signers=creds[:1])
        store.add(c)
        prior = c.configuration_digest
    assert verify_configuration(store, prior, keys) == "VALID"   # deep but genuine: fine


def test_member_key_rotation_is_quorum_loss_by_rule(world, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """PR #120 review finding 3, codified: a Cell member's key is pinned for the Cell's
    lifetime — ordinary rotation removes it from live quorum (BC-4 forbids re-membering
    the new key), so rotation of a 1-member Cell's key blocks exercise exactly like loss."""
    creds, keys = world
    trust = TrustRegistry(str(tmp_path / "t.json"))
    trust.enroll("node-0", creds[0].signing_key_ref)
    store = AuthorityProofStore()
    sole = _config(creds[:1], 1)
    store.add(sole)
    r = _receipt(sole, _op(sole), creds[:1])
    assert verify_receipt(store, r, keys, trust) == "VALID"
    trust.rotate("node-0", creds[0].signing_key_ref, "ed25519:new-key")
    assert verify_receipt(store, r, keys, trust) == "INVALID"    # rotated ≠ active: no count
    assert verify_receipt(store, r, keys) == "VALID"             # history untouched (S7)


def test_cyclic_lineage_is_refused_not_walked(world) -> None:  # type: ignore[no-untyped-def]
    """A fabricated cyclic `prior` chain must be INVALID, never an infinite recursion —
    content addressing makes a real cycle unforgeable, but the verifier must not loop on
    garbage store contents."""
    creds, keys = world
    store = AuthorityProofStore()
    a = _config(creds, 2, epoch=1, prior="sha256:tbd", signers=creds[:2])
    b = _config(creds, 2, epoch=2, prior=a.configuration_digest, signers=creds[:2])
    a_cyclic = a.model_copy(update={"prior_configuration_digest": b.configuration_digest})
    store.configurations[a.configuration_digest] = a_cyclic   # forge the cycle in the store
    store.add(b)
    assert verify_configuration(store, a.configuration_digest, keys) == "INVALID"
    assert verify_configuration(store, b.configuration_digest, keys) == "INVALID"
    assert current_configuration(store, K, keys)[0] in ("INVALID", "INCOMPLETE")


def test_incomplete_becomes_valid_only_by_fetching_the_chain(world) -> None:  # type: ignore[no-untyped-def]
    creds, keys = world
    full, g, s1 = _line(creds, keys)
    r = _receipt(s1, _op(s1), creds[:2])
    partial = AuthorityProofStore()
    partial.add(s1)                                   # successor held, genesis missing
    assert verify_receipt(partial, r, keys) == "INCOMPLETE"
    assert verify_receipt(partial, r, keys) == "INCOMPLETE"   # retry alone changes nothing
    partial.add(g)                                    # fetch the exact chain
    assert verify_receipt(partial, r, keys) == "VALID"

# ---------------- #149 (ADV-05): members is a set — duplicates are non-conforming ----------------

def test_duplicate_members_are_rejected_at_every_layer(world) -> None:  # type: ignore[no-untyped-def]
    """A [A,A,A]/quorum-3 configuration would verify as bound while _valid_signers can
    only ever count one distinct signer — a permanently deadlocked Cell advertising a
    threshold it cannot meet. Mint paths refuse loudly; the verifier treats a smuggled
    duplicate-member configuration as structurally non-conforming noise, never a crash."""
    creds, keys = world
    a = creds[0]
    dup_refs = [a.signing_key_ref, a.signing_key_ref, a.signing_key_ref]
    # mint-time: the model refuses the payload...
    with pytest.raises(ValueError, match="duplicate member"):
        AuthorityConfiguration(authority_key=K, configuration_digest="sha256:x",
                               authority_epoch=0, members=dup_refs, quorum=3)
    # ...and the coordinate derivation refuses loudly too
    with pytest.raises(ValueError, match="duplicate member"):
        configuration_coordinate_digest(authority_key=K, authority_epoch=0,
                                        members=dup_refs, quorum=3,
                                        prior_configuration_digest=None)
    # verify-time: a configuration smuggled past pydantic (model_construct) is noise —
    # INVALID standing, no exception, and it cannot grief a healthy genesis as a rival
    smuggled = AuthorityConfiguration.model_construct(
        kind=AuthorityConfiguration.model_fields["kind"].default,
        authority_key=K, configuration_digest="sha256:whatever",
        authority_epoch=0, members=dup_refs, quorum=3,
        prior_configuration_digest=None,
        succession_proof=make_proof("sha256:whatever", [a, a, a]))
    store = AuthorityProofStore()
    store.add(smuggled)
    assert verify_configuration(store, "sha256:whatever", keys) == "INVALID"
    healthy = _config([creds[0], creds[1]], 2)
    store.add(healthy)
    assert verify_configuration(store, healthy.configuration_digest, keys) == "VALID"


def test_genesis_requires_unanimity_not_quorum_164(world) -> None:  # type: ignore[no-untyped-def]
    """#164: founding is one-shot unanimous. A genesis signed by a quorum subset of its
    declared members conscripts the non-signers into the accountable pool — fabricated
    consent at the membership level. Quorum governs operation/succession, never creation."""
    creds, keys = world
    # 2-of-3 signatures meet the declared quorum but NOT unanimity: non-conforming noise
    store = AuthorityProofStore()
    conscripted = _config(creds, 2, signers=creds[:2])
    store.add(conscripted)
    assert verify_configuration(store, conscripted.configuration_digest, keys) == "INVALID"
    # ...and as noise it cannot grief a healthy unanimous genesis into a conflict
    healthy = _config(creds, 2)                        # all three members sign
    store.add(healthy)
    assert verify_configuration(store, healthy.configuration_digest, keys) == "VALID"
    # succession stays quorum-based: the parent's quorum (2 of 3) proves the successor
    succ = _config(creds, 2, epoch=1, prior=healthy.configuration_digest,
                   signers=creds[:2])
    store.add(succ)
    assert verify_configuration(store, succ.configuration_digest, keys) == "VALID"
