"""Phase 4B trust lifecycle — trust standing SEPARATE from provenance, forward-only. Pure (no DB)."""
from __future__ import annotations

from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.trust import Evaluation, TrustRegistry, admit, evaluate


def _setup(tmp_path):  # type: ignore[no-untyped-def]
    keys = PublicKeyRegistry(str(tmp_path / "keys.json"))
    trust = TrustRegistry(str(tmp_path / "trust.json"))
    a = load_or_create_local_node(str(tmp_path / "a.json"), node_ref="node-a")
    keys.register(a.signing_key_ref, a.public_pem())
    trust.enroll("node-a", a.signing_key_ref)
    return keys, trust, a


def test_revocation_is_forward_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    keys, trust, a = _setup(tmp_path)
    ch = "past-event"
    sig = a.sign(ch)
    before = evaluate(ch, sig, a.signing_key_ref, "ed25519", keys, trust)
    assert before == Evaluation(provenance_valid=True, trust_standing="active")
    assert admit(before) is True

    trust.revoke(a.signing_key_ref)                       # forward-only distrust
    after = evaluate(ch, sig, a.signing_key_ref, "ed25519", keys, trust)
    # provenance is UNCHANGED (the past Event is still cryptographically bound) — history not rewritten;
    # only the CURRENT trust standing changed. The two axes move independently.
    assert after.provenance_valid is True
    assert after.trust_standing == "revoked"
    assert admit(after) is False                          # revoked -> not admitted (fail closed), but valid provenance


def test_rotation_succession_keeps_past_legitimate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    keys, trust, a = _setup(tmp_path)
    ch = "signed-by-A"
    sig_a = a.sign(ch)
    b = load_or_create_local_node(str(tmp_path / "b.json"), node_ref="node-a")   # the node's new key
    keys.register(b.signing_key_ref, b.public_pem())
    trust.rotate("node-a", a.signing_key_ref, b.signing_key_ref)

    assert trust.standing(a.signing_key_ref) == "rotated"   # superseded, not revoked
    assert trust.standing(b.signing_key_ref) == "active"
    past = evaluate(ch, sig_a, a.signing_key_ref, "ed25519", keys, trust)
    assert past.provenance_valid is True and past.trust_standing == "rotated"   # legitimate history
    # revocation is terminal: rotating TO a revoked key is REFUSED (no stale resurrection):
    import pytest
    trust.revoke(b.signing_key_ref)
    with pytest.raises(ValueError):
        trust.rotate("node-a", a.signing_key_ref, b.signing_key_ref)
    assert trust.standing(b.signing_key_ref) == "revoked"   # stays revoked; not resurrected


def test_two_axes_are_distinct(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The whole point: a signature can be cryptographically VALID while the key is NOT currently trusted.
    keys, trust, a = _setup(tmp_path)
    ch = "e"
    sig = a.sign(ch)
    trust.revoke(a.signing_key_ref)
    ev = evaluate(ch, sig, a.signing_key_ref, "ed25519", keys, trust)
    assert ev.provenance_valid is True and ev.trust_standing == "revoked"   # provenance != trust, concretely
    assert admit(ev) is False


def test_key_binds_to_exactly_one_node_forever(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """PR #90 review counterexample, closed: enroll key K for node-a, then rebind K to node-b via
    enroll or rotate — after the overwrite, K-signed events claiming node-b would pass provenance,
    standing AND the node_ref binding check. Rebinding must therefore not exist."""
    import pytest
    keys, trust, a = _setup(tmp_path)                                  # a's key bound to node-a
    with pytest.raises(ValueError):
        trust.enroll("node-b", a.signing_key_ref)                      # rebind via enroll -> refused
    b = load_or_create_local_node(str(tmp_path / "b.json"), node_ref="node-b")
    with pytest.raises(ValueError):
        trust.rotate("node-b", a.signing_key_ref, b.signing_key_ref)   # seize as 'old' -> refused
    trust.enroll("node-b", b.signing_key_ref)
    with pytest.raises(ValueError):
        trust.rotate("node-a", a.signing_key_ref, b.signing_key_ref)   # rebind as 'new' -> refused
    # nothing moved: bindings and standings are exactly as enrolled
    assert trust.node_ref(a.signing_key_ref) == "node-a"
    assert trust.node_ref(b.signing_key_ref) == "node-b"
    assert trust.standing(a.signing_key_ref) == "active"
    assert trust.standing(b.signing_key_ref) == "active"


def test_rotate_from_revoked_key_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Round-1 review follow-up, pinned as REFUSED: rotation is a succession of legitimacy, so it may
    not flow OUT of a revoked key (S7). Re-admitting a node is an explicit fresh enroll, never a
    rotation lineage through revocation."""
    import pytest
    keys, trust, a = _setup(tmp_path)
    trust.revoke(a.signing_key_ref)
    b = load_or_create_local_node(str(tmp_path / "b.json"), node_ref="node-a")
    with pytest.raises(ValueError):
        trust.rotate("node-a", a.signing_key_ref, b.signing_key_ref)
    assert trust.standing(b.signing_key_ref) == "unknown"      # nothing was activated
    assert trust.standing(a.signing_key_ref) == "revoked"      # terminal, untouched


def test_admit_fail_closed_on_every_non_active(tmp_path) -> None:  # type: ignore[no-untyped-def]
    keys, trust, a = _setup(tmp_path)
    ch = "e"
    sig = a.sign(ch)
    assert admit(evaluate(ch, sig, a.signing_key_ref, "ed25519", keys, trust)) is True   # (valid, active)
    # tampered content -> provenance invalid -> reject
    assert admit(evaluate("other", sig, a.signing_key_ref, "ed25519", keys, trust)) is False
    # unknown key ref -> (provenance invalid via missing pubkey, standing unknown) -> reject
    assert admit(evaluate(ch, sig, "ed25519:missing", "ed25519", keys, trust)) is False
