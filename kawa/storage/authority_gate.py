"""The CP gates (#118 PR 3): the event log feeds the pure verifier, and two operations —
fork resolution and policy lineage — exercise authority ONLY through VALID receipts.

Commit order is receipt-first (rev 2 (a)): the `authority.receipt` event is durable,
replicated history BEFORE the operation applies; an operation record whose receipt is not
locally held verifies INCOMPLETE and exercises nothing (BC-3). Replay/crash recovery is
idempotent (BC-2): re-applying the same operation under the same receipt completes it,
never doubles it.
"""
from __future__ import annotations

import psycopg

from kawa.domain.authority import (
    AuthorityProofStore,
    FORK_RESOLUTION_KEY,
    POLICY_KEY,
    fork_resolution_operation_digest,
    policy_operation_digest,
    verify_receipt,
)
from kawa.domain.credential import PublicKeyRegistry
from kawa.domain.events import AuthorityReceipt, EventKind
from kawa.domain.ids import digest
from kawa.domain.participant import CapabilityPolicy
from kawa.domain.trust import TrustRegistry
from kawa.projections.reducers import _load_payload


class AuthorityRefused(RuntimeError):
    """A CP operation did not exercise: the receipt is absent (INCOMPLETE), not VALID,
    mis-bound, or the policy fence rejected the commit. Typed so gates fail closed with
    a reason, never a silent no-op and never a fallback path."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def load_proof_store(conn: psycopg.Connection) -> AuthorityProofStore:
    """The verifier's chain source IS the event log (rev 2 (a)): every locally-held,
    materialized authority.configuration event. Stubs contribute nothing (BC-3: an
    envelope alone is not a configuration you may verify against)."""
    store = AuthorityProofStore()
    with conn.cursor() as cur:
        cur.execute("SELECT event_id FROM events WHERE kind = %s AND materialized",
                    (EventKind.AUTHORITY_CONFIGURATION.value,))
        ids = [r[0] for r in cur.fetchall()]
        for eid in ids:
            store.add(_load_payload(cur, eid, EventKind.AUTHORITY_CONFIGURATION.value))
    return store


def load_receipt(conn: psycopg.Connection, receipt_event_id: str) -> AuthorityReceipt | None:
    """A receipt is usable only as a locally-held, MATERIALIZED event (BC-3)."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM events WHERE event_id = %s AND kind = %s AND materialized",
                    (receipt_event_id, EventKind.AUTHORITY_RECEIPT.value))
        if cur.fetchone() is None:
            return None
        return _load_payload(cur, receipt_event_id, EventKind.AUTHORITY_RECEIPT.value)


def current_policy_digest(conn: psycopg.Connection) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT policy_digest FROM policy_lineage WHERE superseded_by IS NULL")
        rows = cur.fetchall()
    if len(rows) > 1:      # lineage corruption is loud, never a pick
        raise AuthorityRefused("policy_lineage_conflict")
    return rows[0][0] if rows else None


def _exercise(conn: psycopg.Connection, receipt: AuthorityReceipt, *, authority_key: str,
              expected_operation_digest: str, keys: PublicKeyRegistry,
              trust: TrustRegistry) -> AuthorityReceipt:
    """The shared gate over an already-loaded receipt: §6.1 policy fence (rule (a),
    first) → statement binding → three-state verdict at CURRENT trust. Anything but
    VALID refuses. (Callers load the receipt exactly once — PR #121 review.)"""
    if receipt.authority_key != authority_key:
        raise AuthorityRefused("receipt_wrong_key")
    if receipt.policy_digest != current_policy_digest(conn):
        raise AuthorityRefused("policy_superseded")           # §6.1 rule (a), judged FIRST: a
        # receipt pinned to a superseded policy is stale however well it binds — re-initiate
    if receipt.operation_digest != expected_operation_digest:
        raise AuthorityRefused("receipt_operation_mismatch")  # a receipt authorizes ONE operation
    store = load_proof_store(conn)
    standing = verify_receipt(store, receipt, keys, trust)
    if standing != "VALID":
        raise AuthorityRefused(f"receipt_{standing.lower()}")
    return receipt


def resolve_fork(conn: psycopg.Connection, trust: TrustRegistry, *, origin_node: str,
                 origin_seq: int, chosen_head: str, receipt_event_id: str,
                 keys: PublicKeyRegistry, reason: str = "") -> None:
    """Fork-freeze release as a CP operation (#118 10C — D1 answered). The bare-operator
    path DOES NOT EXIST anymore: no VALID receipt, no unfreeze — across restarts, replays,
    and time. `reason` is audit metadata, never authorized semantics (r1 (f)).

    BC-2 (consume-once, idempotent replay): the operation is keyed by its digest; re-running
    under the SAME receipt completes idempotently (already-resolved rows with this receipt
    are a no-op), and the same fork point can never be resolved a second way (the evidence
    row records the consuming receipt)."""
    with conn.cursor() as cur:
        cur.execute("SELECT held_event_id, rival_key_ref, frozen, chosen_head, resolved_by "
                    "FROM security_fork_evidence WHERE origin_node=%s AND origin_seq=%s",
                    (origin_node, origin_seq))
        rows = cur.fetchall()
    if not rows:
        raise AuthorityRefused("no_fork_evidence")
    receipt = load_receipt(conn, receipt_event_id)
    if receipt is None:
        raise AuthorityRefused("receipt_incomplete")          # absent/stub: INCOMPLETE, no exercise
    _exercise(
        conn, receipt, authority_key=FORK_RESOLUTION_KEY,
        expected_operation_digest=fork_resolution_operation_digest(
            origin_node=origin_node, origin_seq=origin_seq, chosen_head=chosen_head,
            configuration_digest=receipt.configuration_digest,
            policy_digest=current_policy_digest(conn)),
        keys=keys, trust=trust)
    with conn.cursor() as cur:
        for held_event_id, rival_key_ref, frozen, prior_chosen, resolved_by in rows:
            if not frozen:
                # BC-2: idempotent completion under the SAME receipt; a DIFFERENT
                # resolution of a consumed fork point is refused, never re-decided
                if prior_chosen == chosen_head and resolved_by == receipt_event_id:
                    continue
                raise AuthorityRefused("fork_already_resolved")
            if chosen_head != held_event_id:
                raise AuthorityRefused("rival_adoption_deferred")   # D2: step-10 addendum land
            if rival_key_ref is not None:
                trust.revoke(rival_key_ref, from_seq=origin_seq)    # BC-3 (step 8): scoped
        cur.execute(
            "UPDATE security_fork_evidence SET frozen=false, resolved_by=%s, "
            "resolved_at=clock_timestamp(), chosen_head=%s, reason=%s "
            "WHERE origin_node=%s AND origin_seq=%s AND frozen",
            (receipt_event_id, chosen_head, reason, origin_node, origin_seq),
        )
    conn.commit()


def establish_policy(conn: psycopg.Connection, trust: TrustRegistry, *, canonical_bundle: str,
                     receipt_event_id: str, keys: PublicKeyRegistry) -> str:
    """`policy.establish` / `policy.supersede` as ONE receipt-gated CP operation (10D):
    establishing a bundle supersedes the current head (if any). Returns the new digest.
    Concurrent establishments from the same prior cannot both land: the single-head
    partial unique index makes the loser error instead of minting a multi-head lineage
    (PR #121 review)."""
    policy_digest = digest(canonical_bundle)
    prior = current_policy_digest(conn)
    if prior == policy_digest:
        return policy_digest                                  # idempotent re-establishment
    op = "policy.establish" if prior is None else "policy.supersede"
    receipt = load_receipt(conn, receipt_event_id)
    if receipt is None:
        raise AuthorityRefused("receipt_incomplete")
    _exercise(conn, receipt, authority_key=POLICY_KEY,
              expected_operation_digest=policy_operation_digest(
                  op=op, policy_digest=policy_digest, prior_policy_digest=prior,
                  configuration_digest=receipt.configuration_digest),
              keys=keys, trust=trust)
    with conn.cursor() as cur:
        # supersede the old head FIRST — the single-head unique index forbids two current
        # rows even transiently, which is exactly the race protection
        if prior is not None:
            cur.execute("UPDATE policy_lineage SET superseded_by=%s WHERE policy_digest=%s "
                        "AND superseded_by IS NULL", (policy_digest, prior))
            if cur.rowcount != 1:
                conn.rollback()
                raise AuthorityRefused("policy_lineage_races")   # someone else superseded first
        cur.execute(
            "INSERT INTO policy_lineage (policy_digest, canonical_bundle, "
            "established_receipt_digest, prior_policy_digest) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (policy_digest) DO NOTHING",
            (policy_digest, canonical_bundle, receipt_event_id, prior),
        )
    conn.commit()
    return policy_digest


def capability_policy_from_lineage(conn: psycopg.Connection) -> CapabilityPolicy | None:
    """participant.py §16.2, D6 answered: the current-authorization source reads the
    receipt-gated lineage head. None = no policy established (callers fail closed; the
    fixture profile remains for tests, stated)."""
    import json
    with conn.cursor() as cur:
        cur.execute("SELECT canonical_bundle FROM policy_lineage WHERE superseded_by IS NULL")
        rows = cur.fetchall()
    if len(rows) != 1:
        return None
    bundle = json.loads(rows[0][0])
    return CapabilityPolicy(authorized=frozenset(bundle.get("authorized", [])))