"""Node trust lifecycle (Phase 4B + step 8 incarnation lineage) — current trust standing,
SEPARATE from cryptographic provenance.

`credential.py` answers provenance: "is this Event cryptographically bound to a key?" — a fact about
the past that never changes. This module answers trust: "is that key *currently* trusted?" — a mutable,
**forward-only** evaluation (③④ S7: distrust is forward-only; it changes current/future standing, never
rewrites history). Revoking a key does NOT invalidate the provenance of Events it already signed; it
withdraws *current* trust from that point forward. Two axes; an admission decision consults both and
never conflates them (`signature valid` is never `trusted`).

Step 8 (v0.5 §13, issue #111): Node Incarnation is carried as **key lineage** in this registry — no
envelope column. Every key binds to exactly one `(node, incarnation)`; `rotate` continues the SAME
incarnation (key hygiene), `succeed_incarnation` starts a NEW one and mandates a FRESH key with
recorded parentage. The two verbs are disjoint by construction, so attributing any authenticated
Event to the incarnation that signed it is total and unambiguous.

BC-3 (#111 r2): `resolve_fork` needs revocation scoped to the fork point — `revoke(key, from_seq=N)`
withdraws trust for events at `origin_seq >= N` while pre-fork trunk events that replicate late still
evaluate against the key's pre-revocation standing. Plain `revoke(key)` stays total (strict default).

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


def genesis_incarnation(node_ref: str) -> str:
    """The implicit first incarnation of a node that never declared one. Deterministic so
    legacy enrollments (pre-step-8 flat registries) attribute consistently on every load."""
    return f"inc:{node_ref}:genesis"


class TrustRegistry:
    """`key_ref -> (node, incarnation, current trust standing)`. Forward-only: revoke/rotate change
    current standing; nothing here deletes or rewrites a past Event. Separate from the provenance
    (pubkey) registry by design.

    On-disk format: `{"keys": {key_ref: {...}}, "incarnations": {inc_ref: {node_ref, parent}}}`.
    A legacy flat file (pre-step-8: `{key_ref: {node_ref, standing}}`) loads transparently; its
    keys attribute to the node's deterministic genesis incarnation."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._m: dict[str, dict] = {}
        self._inc: dict[str, dict] = {}
        if os.path.exists(path):
            data = json.loads(open(path, encoding="utf-8").read())
            if "keys" in data or "incarnations" in data:
                self._m = data.get("keys", {})
                self._inc = data.get("incarnations", {})
            else:  # legacy flat format
                self._m = data
        for entry in self._m.values():  # legacy entries: synthesize genesis attribution
            if "incarnation_ref" not in entry:
                inc = genesis_incarnation(entry["node_ref"])
                entry["incarnation_ref"] = inc
                self._inc.setdefault(inc, {"node_ref": entry["node_ref"], "parent": None})

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"keys": self._m, "incarnations": self._inc}, f)

    def enroll(self, node_ref: str, key_ref: str, incarnation_ref: str | None = None) -> None:
        if self.standing(key_ref) == "revoked":
            raise ValueError("cannot enroll a revoked key — revocation is forward-only and terminal")
        self._require_unbound_or_same(node_ref, key_ref)
        inc = incarnation_ref or genesis_incarnation(node_ref)
        existing_inc = self._inc.get(inc)
        if existing_inc is not None and existing_inc["node_ref"] != node_ref:
            raise ValueError(f"incarnation {inc!r} belongs to {existing_inc['node_ref']!r} — "
                             "an incarnation binds to exactly one node")
        self._inc.setdefault(inc, {"node_ref": node_ref, "parent": None})
        self._m[key_ref] = {"node_ref": node_ref, "standing": "active", "incarnation_ref": inc}
        self._save()

    def succeed_incarnation(self, node_ref: str, parent_incarnation: str,
                            new_incarnation: str, new_key_ref: str) -> None:
        """Start a NEW incarnation of `node_ref` (restore / migration / intentional fork — §13).

        Disjoint from `rotate` by construction: succession mandates a FRESH key (never previously
        bound — key reuse would alias two incarnations under one attribution key), records the
        parentage evidence, and never crosses nodes. Divergence between incarnations then classifies
        as restore-fork, not equivocation; a clone that skips this and reuses the parent's key IS
        the same incarnation, and its divergence is equivocation — intended semantics (#111 rev 2)."""
        if new_key_ref in self._m:
            raise ValueError("incarnation succession requires a FRESH key — this key is already "
                             "bound; reusing a key would alias two incarnations")
        if self.standing(new_key_ref) == "revoked":
            raise ValueError("cannot succeed onto a revoked key — no stale resurrection (③④ S5)")
        parent = self._inc.get(parent_incarnation)
        if parent is None:
            raise ValueError(f"unknown parent incarnation {parent_incarnation!r} — succession "
                             "requires recorded parentage, never inferred")
        if parent["node_ref"] != node_ref:
            raise ValueError(f"parent incarnation belongs to {parent['node_ref']!r} — succession "
                             "never crosses nodes")
        if new_incarnation in self._inc:
            raise ValueError(f"incarnation {new_incarnation!r} already exists — an incarnation is "
                             "minted once")
        self._inc[new_incarnation] = {"node_ref": node_ref, "parent": parent_incarnation}
        self._m[new_key_ref] = {"node_ref": node_ref, "standing": "active",
                                "incarnation_ref": new_incarnation}
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

    def revoke(self, key_ref: str, from_seq: int | None = None) -> None:
        """Forward-only distrust (③④ S7). Marks the key revoked; past Events keep valid provenance —
        their *current* trust is withdrawn, but history is not touched.

        BC-3 (#111 r2): `from_seq` scopes the revocation to a fork point — events the key signed at
        `origin_seq < from_seq` (the shared pre-fork trunk) still evaluate against the key's
        pre-revocation standing when admission passes the event's seq, so a lagging replica can
        finish verifying legitimate trunk history. Only `resolve_fork` should pass `from_seq`;
        plain `revoke(key)` stays total and strict. Repeat revocations only ever STRICTEN
        (PR #112 review finding 2): a total `revoke(key)` on a fork-scoped revocation
        escalates it to total (the scope is dropped); a scoped call on an existing
        revocation never narrows it back."""
        if key_ref in self._m:
            entry = self._m[key_ref]
            if entry["standing"] != "revoked":
                if from_seq is not None:
                    entry["revoked_from_seq"] = from_seq
                    entry["prior_standing"] = entry["standing"]
                entry["standing"] = "revoked"
            elif from_seq is None:                       # escalate scoped -> total, one-way
                entry.pop("revoked_from_seq", None)
                entry.pop("prior_standing", None)
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
        # rotation continues the SAME incarnation — key hygiene, never a continuity boundary (§13).
        inc = old["incarnation_ref"] if old is not None else genesis_incarnation(node_ref)
        self._inc.setdefault(inc, {"node_ref": node_ref, "parent": None})
        self._m[new_key_ref] = {"node_ref": node_ref, "standing": "active", "incarnation_ref": inc}
        self._save()

    def standing(self, key_ref: str, at_seq: int | None = None) -> TrustStanding:
        """Current standing; absent -> unknown (caller fails closed).

        `at_seq` (BC-3): when the caller is judging a specific event and the key's revocation is
        fork-scoped (`revoked_from_seq`), events strictly before the fork point evaluate against
        the key's pre-revocation standing. A totally-revoked key (no `revoked_from_seq`) is
        revoked at every seq — the strict default."""
        entry = self._m.get(key_ref)
        if entry is None:
            return "unknown"
        if (entry["standing"] == "revoked" and at_seq is not None
                and entry.get("revoked_from_seq") is not None
                and at_seq < entry["revoked_from_seq"]):
            return entry.get("prior_standing", "revoked")
        return entry["standing"]

    def incarnation_ref(self, key_ref: str) -> str | None:
        """Which incarnation this key speaks for — the §13 attribution (total: every bound key
        has one; rotation preserves it, succession mints it with a fresh key)."""
        entry = self._m.get(key_ref)
        return entry["incarnation_ref"] if entry else None

    def incarnation_parent(self, incarnation_ref: str) -> str | None:
        entry = self._inc.get(incarnation_ref)
        return entry["parent"] if entry else None

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
             keys: PublicKeyRegistry, trust: TrustRegistry, at_seq: int | None = None) -> Evaluation:
    """Return provenance and trust standing SEPARATELY — never conflated.

    Provenance comes from the crypto (resolve_and_verify_provenance); trust from the registry. An
    admission/replication decision uses BOTH — e.g. accept iff `provenance_valid AND
    trust_standing == 'active'`. A provenance-valid-but-revoked Event is NOT accepted as unknown: it is
    explicitly `(valid, revoked)`, and admission fails closed on anything other than `(valid, active)`.
    """
    prov = resolve_and_verify_provenance(content_hash, signature, signing_key_ref, signature_scheme, keys)
    return Evaluation(provenance_valid=prov, trust_standing=trust.standing(signing_key_ref, at_seq=at_seq))


def admit(evaluation: Evaluation) -> bool:
    """Fail-closed admission over the two axes: accept ONLY (provenance_valid=True, standing='active').
    Every other combination — invalid provenance, or valid provenance under a rotated/revoked/unknown
    key — is rejected. Never treated as 'unknown-accept'."""
    return evaluation.provenance_valid and evaluation.trust_standing == "active"
