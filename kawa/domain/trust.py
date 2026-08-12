"""Node trust lifecycle (Phase 4B) — current trust standing, SEPARATE from cryptographic provenance.

`credential.py` answers provenance: "is this Event cryptographically bound to a key?" — a fact about
the past that never changes. This module answers trust: "is that key *currently* trusted?" — a mutable,
**forward-only** evaluation (③④ S7: distrust is forward-only; it changes current/future standing, never
rewrites history). Revoking a key does NOT invalidate the provenance of Events it already signed; it
withdraws *current* trust from that point forward. Two axes; an admission decision consults both and
never conflates them (`signature valid` is never `trusted`).

The trust registry is deliberately a SEPARATE store from the pubkey (provenance) registry: pubkey
retention exists so past Events stay verifiable; trust standing is a distinct, policy-side judgement.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Literal

from kawa.domain.credential import PublicKeyRegistry, resolve_and_verify_provenance

TrustStanding = Literal["active", "rotated", "revoked", "unknown"]


class TrustRegistry:
    """`key_ref -> current trust standing`. Forward-only: revoke/rotate change current standing; nothing
    here deletes or rewrites a past Event. Separate from the provenance (pubkey) registry by design."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._m: dict[str, dict] = json.loads(open(path, encoding="utf-8").read()) if os.path.exists(path) else {}

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._m, f)

    def enroll(self, node_ref: str, key_ref: str) -> None:
        if self.standing(key_ref) == "revoked":
            raise ValueError("cannot enroll a revoked key — revocation is forward-only and terminal")
        self._require_unbound_or_same(node_ref, key_ref)
        self._m[key_ref] = {"node_ref": node_ref, "standing": "active"}
        self._save()

    def _require_unbound_or_same(self, node_ref: str, key_ref: str) -> None:
        """A key_ref binds to exactly ONE node, permanently. Silent rebinding would let a key
        enrolled for node-a later pass admission speaking as node-b (PR #90 review counterexample) —
        so any enroll/rotate touching a key bound to a DIFFERENT node is refused. Moving a node to
        a new key is rotation; moving a key to a new node does not exist (revoke + issue a new key)."""
        existing = self._m.get(key_ref)
        if existing is not None and existing["node_ref"] != node_ref:
            raise ValueError(
                f"key is bound to {existing['node_ref']!r} — a key binds to exactly one node; "
                "rebinding is refused (revoke and issue a new key instead)"
            )

    def revoke(self, key_ref: str) -> None:
        """Forward-only distrust (③④ S7). Marks the key revoked; past Events keep valid provenance —
        their *current* trust is withdrawn, but history is not touched."""
        if key_ref in self._m:
            self._m[key_ref]["standing"] = "revoked"
            self._save()

    def rotate(self, node_ref: str, old_key_ref: str, new_key_ref: str) -> None:
        """Key succession: the superseded key becomes `rotated` (its past Events remain legitimate — it
        was the node's key when it signed them); the new key is `active`. A revoked key is terminal: enroll/rotate
        refuse to reactivate it (no stale resurrection)."""
        if self.standing(new_key_ref) == "revoked":
            raise ValueError("cannot rotate to a revoked key — no stale resurrection (③④ S5)")
        if self.standing(old_key_ref) == "revoked":
            raise ValueError("cannot rotate FROM a revoked key — succession would launder legitimacy "
                             "out of a distrusted key (S7); re-admitting a node is an explicit fresh "
                             "enroll, never a rotation lineage through revocation")
        self._require_unbound_or_same(node_ref, old_key_ref)   # may not seize another node's key's standing
        self._require_unbound_or_same(node_ref, new_key_ref)   # may not rebind another node's key to self
        old = self._m.get(old_key_ref)
        if old is not None and old["standing"] == "active":
            old["standing"] = "rotated"
        self._m[new_key_ref] = {"node_ref": node_ref, "standing": "active"}
        self._save()

    def standing(self, key_ref: str) -> TrustStanding:
        entry = self._m.get(key_ref)
        return entry["standing"] if entry else "unknown"  # absent -> unknown (caller fails closed)

    def node_ref(self, key_ref: str) -> str | None:
        """Which node this key is enrolled/rotated FOR. Replication admission must check the Event's
        claimed `origin_node` against this binding: a signature by a *trusted* key still forges origin
        if the key belongs to a different node ('trusted key' never means 'may speak as anyone')."""
        entry = self._m.get(key_ref)
        return entry["node_ref"] if entry else None


@dataclass(frozen=True)
class Evaluation:
    """The two axes, kept SEPARATE. `provenance_valid` is an immutable cryptographic fact; `trust_standing`
    is the mutable, forward-only current-trust judgement. A revoked key can be `provenance_valid=True`."""

    provenance_valid: bool
    trust_standing: TrustStanding


def evaluate(content_hash: str, signature: str, signing_key_ref: str, signature_scheme: str,
             keys: PublicKeyRegistry, trust: TrustRegistry) -> Evaluation:
    """Return provenance and trust standing SEPARATELY — never conflated.

    Provenance comes from the crypto (resolve_and_verify_provenance); trust from the registry. An
    admission/replication decision uses BOTH — e.g. accept iff `provenance_valid AND
    trust_standing == 'active'`. A provenance-valid-but-revoked Event is NOT accepted as unknown: it is
    explicitly `(valid, revoked)`, and admission fails closed on anything other than `(valid, active)`.
    """
    prov = resolve_and_verify_provenance(content_hash, signature, signing_key_ref, signature_scheme, keys)
    return Evaluation(provenance_valid=prov, trust_standing=trust.standing(signing_key_ref))


def admit(evaluation: Evaluation) -> bool:
    """Fail-closed admission over the two axes: accept ONLY (provenance_valid=True, standing='active').
    Every other combination — invalid provenance, or valid provenance under a rotated/revoked/unknown
    key — is rejected. Never treated as 'unknown-accept'."""
    return evaluation.provenance_valid and evaluation.trust_standing == "active"
