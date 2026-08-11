"""Phase 4A attestation — cryptographic provenance, and its separation from trust. Pure (no DB)."""
from __future__ import annotations

from kawa.domain.credential import (
    PublicKeyRegistry,
    load_or_create_local_node,
    resolve_and_verify_provenance,
    verify_provenance,
)


def test_provenance_roundtrip_and_tamper(tmp_path) -> None:  # type: ignore[no-untyped-def]
    c = load_or_create_local_node(str(tmp_path / "id.json"), node_ref="node-a")
    ch = "content-hash-abc"
    sig = c.sign(ch)
    assert verify_provenance(ch, sig, c.public_pem()) is True                 # bound to this key
    assert verify_provenance("content-hash-XYZ", sig, c.public_pem()) is False  # tampered content
    other = load_or_create_local_node(str(tmp_path / "id2.json"))
    assert verify_provenance(ch, sig, other.public_pem()) is False            # wrong key


def test_fail_closed_on_bad_scheme_sig_or_missing_key(tmp_path) -> None:  # type: ignore[no-untyped-def]
    c = load_or_create_local_node(str(tmp_path / "id.json"))
    ch = "h"
    sig = c.sign(ch)
    assert verify_provenance(ch, sig, c.public_pem(), signature_scheme="pgp-x") is False  # unknown scheme
    assert verify_provenance(ch, "not-hex", c.public_pem()) is False                       # malformed sig
    reg = PublicKeyRegistry(str(tmp_path / "reg.json"))
    assert resolve_and_verify_provenance(ch, sig, "ed25519:missing", "ed25519", reg) is False  # unknown ref


def test_rotation_keeps_past_events_verifiable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    a = load_or_create_local_node(str(tmp_path / "a.json"))
    reg = PublicKeyRegistry(str(tmp_path / "reg.json"))
    reg.register(a.signing_key_ref, a.public_pem())
    ch = "past-event-hash"
    sig = a.sign(ch)                                       # a past Event signed by key A
    b = load_or_create_local_node(str(tmp_path / "b.json"))   # rotate to a different key B
    reg.register(b.signing_key_ref, b.public_pem())
    assert a.signing_key_ref != b.signing_key_ref
    # A is retired, but the past Event carries A's signing_key_ref and STILL verifies (registry retains A):
    assert resolve_and_verify_provenance(ch, sig, a.signing_key_ref, "ed25519", reg) is True


def test_provenance_is_not_trust(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A signature can be cryptographically VALID while conferring NO trust. verify_provenance takes no
    # policy / revocation / lineage input — it only answers "bound to this key". Key retention in the
    # registry is provenance-resolution, NOT a trust list: a key one would consider revoked still yields
    # valid PROVENANCE, because *current trust standing* is ③④'s separate evaluation, not asserted here.
    c = load_or_create_local_node(str(tmp_path / "id.json"))
    ch = "h"
    sig = c.sign(ch)
    reg = PublicKeyRegistry(str(tmp_path / "reg.json"))
    reg.register(c.signing_key_ref, c.public_pem())
    assert resolve_and_verify_provenance(ch, sig, c.signing_key_ref, "ed25519", reg) is True
    # no trust API exists on this path by construction — provenance != current trust standing.
