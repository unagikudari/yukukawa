"""The authority core: configurations, proven succession, and the three-state verifier
(#118 10A/10B — the Phase-0 realization of the FROZEN `consistency-and-authority-v0.1.md`).

PURE domain logic: every function judges explicit inputs (an in-memory proof store + a
pubkey registry) — no clock, no database, no network. PR 3 wires the store to the event
log. The load-bearing separations, straight from the slab:

    cryptographic validity ≠ lineage validity ≠ current authoritative standing

and the three-state answer:

    VALID       — statement bound + configuration proven + quorum met by current-lineage
                  members + (at the gate) policy pinned
    INVALID     — held evidence proves failure (bad proof, wrong members, superseded or
                  conflicted lineage, structural non-conformance)
    INCOMPLETE  — a required durable proof is not locally held. Absorbing w.r.t. safety:
                  it may become VALID only by fetching and validating the exact chain,
                  and it can NEVER exercise authority (BC-3).

Phase-0 profile (slab §8 — replaceable mechanics, contract preserved): individual Ed25519
signatures + an explicit signer set (accountable — a proof without a signer set is
structurally non-conforming, refused before any cryptography); in-process proof collection
(a proof-collection profile, NEVER a consensus substitute — no proposal ordering or
liveness is claimed). Membership is FIXED across succession in Phase-0 (BC-4): a
membership-changing succession is structurally INVALID; multi-member Cells are
genesis-only until the membership addendum.

Conflict posture (rev 2 (b)): the physically possible S1 violation — two deployments
independently minting configurations for the same coordinate over non-shared state — is
DETECTED, never resolved: two genesis records for one authority_key, or two facially-valid
successors of one parent, block BOTH lines (fail-closed both ways; no last-wins, no
second live Cell) until an operator/recovery decision that is out of Phase-0 scope.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from kawa.domain.credential import PublicKeyRegistry, resolve_and_verify_provenance
from kawa.domain.events import AuthorityConfiguration, AuthorityReceipt
from kawa.domain.ids import canonical_json, digest
from kawa.domain.trust import TrustRegistry

Standing = Literal["VALID", "INVALID", "INCOMPLETE"]

FORK_RESOLUTION_KEY = "authority:fork-resolution"
POLICY_KEY = "authority:policy"


# ---- canonical coordinates: machine-stable bytes ONLY (#118 r1 (f)) ----

def configuration_coordinate_digest(*, authority_key: str, authority_epoch: int,
                                    members: list[str], quorum: int,
                                    prior_configuration_digest: str | None) -> str:
    """The content address of one configuration. Members are canonicalized SORTED — a
    configuration is a set with a quorum rule, not an ordering. Duplicate members are
    refused loudly at MINT time (#149); the VERIFY path never reaches this raise —
    `_bound` rejects duplicate-member configurations as structurally non-conforming
    (False → noise) before recomputing the coordinate, keeping verification fail-closed
    instead of fail-crashed."""
    if len(set(members)) != len(members):
        raise ValueError("duplicate member refs — a configuration's members are a set")
    return digest({
        "authority_key": authority_key,
        "authority_epoch": authority_epoch,
        "members": sorted(members),
        "quorum": quorum,
        "prior_configuration_digest": prior_configuration_digest,
    })


def fork_resolution_operation_digest(*, origin_node: str, origin_seq: int, chosen_head: str,
                                     configuration_digest: str,
                                     policy_digest: str | None) -> str:
    """Free prose (reason etc.) is METADATA, never authorized semantics — it does not enter
    this digest and cannot re-bind a receipt (#118 r1 (f))."""
    return digest({
        "op": "fork.resolve",
        "origin_node": origin_node,
        "origin_seq": origin_seq,
        "chosen_head": chosen_head,
        "configuration_digest": configuration_digest,
        "policy_digest": policy_digest,
    })


def policy_operation_digest(*, op: Literal["policy.establish", "policy.supersede"],
                            policy_digest: str, prior_policy_digest: str | None,
                            configuration_digest: str) -> str:
    return digest({
        "op": op,
        "policy_digest": policy_digest,
        "prior_policy_digest": prior_policy_digest,
        "configuration_digest": configuration_digest,
    })


# ---- proofs: accountable signer sets (slab §8 — bare aggregates are non-conforming) ----

def make_proof(statement: str, credentials: list) -> str:
    """Sign `statement` with each credential; the signer set rides WITH the signatures so
    culpability is attributable (#21 distrust needs names, not an opaque threshold blob)."""
    return canonical_json({
        "signer_set": [c.signing_key_ref for c in credentials],
        "signatures": [c.sign(statement) for c in credentials],
    })


def _valid_signers(proof_json: str | None, statement: str, allowed: list[str],
                   keys: PublicKeyRegistry) -> set[str] | None:
    """The distinct ALLOWED signers whose signature over `statement` verifies; None when the
    proof is structurally non-conforming (missing/unparseable/no signer set). A bad or
    stray signature simply does not count — quorum arithmetic is over valid ones."""
    if not proof_json:
        return None
    try:
        proof = json.loads(proof_json)
        signer_set = proof["signer_set"]
        signatures = proof["signatures"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(signer_set, list) or not isinstance(signatures, list) \
            or len(signer_set) != len(signatures):
        return None
    valid: set[str] = set()
    for key_ref, sig in zip(signer_set, signatures):
        if key_ref in allowed and isinstance(sig, str) \
                and resolve_and_verify_provenance(statement, sig, key_ref, "ed25519", keys):
            valid.add(key_ref)
    return valid


# ---- the proof store: what this node durably holds (PR 3 loads it from the event log) ----

@dataclass
class AuthorityProofStore:
    """Everything the verifier may consult — explicit, local, replicated-in via the event
    log. `configurations` is keyed by configuration_digest."""

    configurations: dict[str, AuthorityConfiguration] = field(default_factory=dict)

    def add(self, cfg: AuthorityConfiguration) -> None:
        self.configurations[cfg.configuration_digest] = cfg

    def genesis_of(self, authority_key: str) -> list[AuthorityConfiguration]:
        return [c for c in self.configurations.values()
                if c.authority_key == authority_key and c.prior_configuration_digest is None]

    def successors_of(self, configuration_digest: str) -> list[AuthorityConfiguration]:
        return [c for c in self.configurations.values()
                if c.prior_configuration_digest == configuration_digest]


# ---- configuration standing ----

def _bound(cfg: AuthorityConfiguration, configuration_digest: str) -> bool:
    """Statement binding: the digest must BE the canonical coordinate (a lying digest is
    a different configuration, not a variant of this one), and the quorum must be sane.
    Duplicate members (#149) are structurally non-conforming — False (noise), checked
    BEFORE the coordinate recomputation so the verifier fails closed, never crashes."""
    if len(set(cfg.members)) != len(cfg.members):
        return False
    return (configuration_coordinate_digest(
                authority_key=cfg.authority_key, authority_epoch=cfg.authority_epoch,
                members=cfg.members, quorum=cfg.quorum,
                prior_configuration_digest=cfg.prior_configuration_digest)
            == configuration_digest and 1 <= cfg.quorum <= len(cfg.members))


def _facially_valid_successor(store: AuthorityProofStore, cfg: AuthorityConfiguration,
                              parent: AuthorityConfiguration,
                              keys: PublicKeyRegistry) -> bool:
    """Is `cfg` a REAL succession claim on `parent` — bound, member-preserving (BC-4),
    epoch-consecutive, and carrying a genuine parent-quorum proof? PR #120 review
    blocker 2: only facially-valid successors may count as COMPETITION. Garbage a
    malicious enrolled node sprays into the store is noise to ignore, not a rival that
    blocks the legitimate line — otherwise one junk event griefs the Cell forever.
    (Two facially-valid rivals ARE parent-quorum equivocation: both lines block.)"""
    if not _bound(cfg, cfg.configuration_digest):
        return False
    if sorted(cfg.members) != sorted(parent.members):
        return False
    if cfg.authority_epoch != parent.authority_epoch + 1:
        return False
    signers = _valid_signers(cfg.succession_proof, cfg.configuration_digest,
                             parent.members, keys)
    return signers is not None and len(signers) >= parent.quorum


def _facially_valid_genesis(cfg: AuthorityConfiguration, keys: PublicKeyRegistry) -> bool:
    """A REAL genesis claim: bound, epoch 0, founding-quorum-signed (BC-1). Unsigned junk
    is noise, never an `authority_genesis_conflict` rival (the same anti-griefing rule)."""
    if not _bound(cfg, cfg.configuration_digest) or cfg.authority_epoch != 0:
        return False
    signers = _valid_signers(cfg.succession_proof, cfg.configuration_digest,
                             cfg.members, keys)
    return signers is not None and len(signers) >= cfg.quorum


def verify_configuration(store: AuthorityProofStore, configuration_digest: str,
                         keys: PublicKeyRegistry) -> Standing:
    """Lineage standing of ONE configuration: its own binding + proof + its whole ancestry.
    Conflicts anywhere on the line (duplicate FACIALLY-VALID genesis, competing
    FACIALLY-VALID successors) are INVALID — fail-closed both ways, never a race
    (rev 2 (b)) — while unverifiable garbage is ignored as noise (review blocker 2).

    ITERATIVE (review blocker 1): the walk collects the ancestry path first — a cyclic or
    absurdly deep fabricated `prior` chain is refused by the walk itself, never a
    RecursionError; nothing recurses before anything is validated."""
    path: list[str] = []
    on_path: set[str] = set()
    cursor = configuration_digest
    while True:
        if cursor in on_path:
            return "INVALID"                          # fabricated cyclic lineage
        cfg = store.configurations.get(cursor)
        if cfg is None:
            return "INCOMPLETE"                       # lineage not held — absorbing, never guessed
        path.append(cursor)
        on_path.add(cursor)
        if cfg.prior_configuration_digest is None:
            break
        cursor = cfg.prior_configuration_digest
    # validate root-first: genesis, then each succession link (S2: legitimacy inherited)
    for i, dig in enumerate(reversed(path)):
        cfg = store.configurations[dig]
        if cfg.prior_configuration_digest is None:
            if not _facially_valid_genesis(cfg, keys):
                return "INVALID"
            rivals = [g for g in store.genesis_of(cfg.authority_key)
                      if _facially_valid_genesis(g, keys)]
            if len(rivals) > 1:
                return "INVALID"                      # authority_genesis_conflict: both block
        else:
            parent = store.configurations[cfg.prior_configuration_digest]
            if not _facially_valid_successor(store, cfg, parent, keys):
                return "INVALID"
            rivals = [s for s in store.successors_of(parent.configuration_digest)
                      if _facially_valid_successor(store, s, parent, keys)]
            if len(rivals) > 1:
                return "INVALID"                      # parent-quorum equivocation: both block
    return "VALID"


def current_configuration(store: AuthorityProofStore, authority_key: str,
                          keys: PublicKeyRegistry) -> tuple[Standing, AuthorityConfiguration | None]:
    """The currently-authoritative configuration for a key: the unique valid head of the
    unique valid chain. ("INVALID", None) = a conflict/blocked key; ("INCOMPLETE", None) =
    the chain is not locally complete; ("INVALID", None) with no genesis = no authority
    exists (nothing confers standing, S2)."""
    genesis = [g for g in store.genesis_of(authority_key)
               if _facially_valid_genesis(g, keys)]   # junk genesis is noise (blocker 2)
    if not genesis:
        # Holding part of a chain whose genesis we lack is a MISSING PROOF, not evidence
        # of failure: INCOMPLETE (fetch the chain). Holding nothing at all for the key
        # means no authority exists — nothing confers standing (S2).
        if any(c.authority_key == authority_key for c in store.configurations.values()):
            return "INCOMPLETE", None
        return "INVALID", None
    if len(genesis) > 1:
        return "INVALID", None                        # genesis conflict: the key is blocked
    head = genesis[0]
    seen = {head.configuration_digest}
    while True:
        succ = [s for s in store.successors_of(head.configuration_digest)
                if _facially_valid_successor(store, s, head, keys)]   # junk is noise
        if not succ:
            return "VALID", head
        if succ[0].configuration_digest in seen:
            return "INVALID", None                    # a forward cycle is fabricated lineage
        seen.add(succ[0].configuration_digest)
        if len(succ) > 1:
            return "INVALID", None                    # facially-valid rivals: blocked, never a race
        head = succ[0]


# ---- receipt standing (exercise-time judgement) ----

def verify_receipt(store: AuthorityProofStore, receipt: AuthorityReceipt,
                   keys: PublicKeyRegistry, trust: TrustRegistry | None = None) -> Standing:
    """May this receipt exercise authority NOW? — the slab §5.1 three-state verifier.

    A cryptographically perfect certificate under a stale, superseded, forked, or
    conflicted configuration confers nothing: INVALID where evidence is held, INCOMPLETE
    where the lineage is not locally known (and INCOMPLETE never exercises — BC-3).
    Historical validity of already-exercised receipts is untouched (S7): this judges
    exercise, it does not rewrite audit history.

    `trust` (S7's forward edge): when supplied, only members whose keys are CURRENTLY
    trusted (`standing == 'active'`) count toward quorum for a NEW exercise — revoking the
    sole member of a 1-member Cell is catastrophic quorum loss, and this is exactly where
    it bites: the receipt stops exercising, forever, with no operator fallback (r1 (c)).

    **Phase-0 rule (PR #120 review finding 3): a Cell member's signing key is PINNED for
    the Cell's lifetime.** Rotating it makes the old key `rotated` (≠ active) and BC-4
    forbids re-membering the new key — so rotation is quorum LOSS, deliberately, not a
    silent hole. Cells survive member-key rotation only via the membership addendum
    (successor countersign), never via key drift under a fixed member list."""
    cfg = store.configurations.get(receipt.configuration_digest)
    if cfg is None:
        return "INCOMPLETE"
    if cfg.authority_key != receipt.authority_key:
        return "INVALID"
    if cfg.authority_epoch != receipt.authority_epoch:
        return "INVALID"
    current_standing, current = current_configuration(store, receipt.authority_key, keys)
    if current_standing == "INCOMPLETE":
        return "INCOMPLETE"
    if current_standing == "INVALID" or current is None:
        return "INVALID"                              # blocked/conflicted key exercises nothing
    if current.configuration_digest != receipt.configuration_digest:
        return "INVALID"                              # S5: a superseded config cannot exercise
    signers = _valid_signers(receipt.quorum_proof, receipt.operation_digest, cfg.members, keys)
    if signers is None:
        return "INVALID"                              # no accountable signer set: non-conforming
    if trust is not None:
        signers = {s for s in signers if trust.standing(s) == "active"}
    if len(signers) < cfg.quorum:
        return "INVALID"
    return "VALID"
