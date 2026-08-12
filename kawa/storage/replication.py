"""Cursor-based peer replication with trust-gated admission (#73 Phase 4C, event-log-and-replication §4).

Replication is Kawa's own protocol over the event log — a cursor diff, not a database mesh:

    frontier      what the receiver already holds: {origin_node -> highest contiguous origin_seq}
    read_stream   what a peer serves beyond that frontier (Events WITH their provenance columns)
    pull          receive: verify each Event, consult trust, append-only INSERT + reduce

Admission is the point of this module, and it is **fail-closed on both axes** (③④): an Event is
admitted iff its per-origin chain is intact AND `trust.admit(evaluate(...))` holds AND the signing
key is enrolled FOR the claimed `origin_node`. Everything else — unsigned, invalid signature,
unknown/rotated/revoked key, a trusted key speaking as another node, a chain gap — is a typed
`Rejection`, never a silent skip and never an insert. Distrust stays forward-only: rejecting a
revoked origin's NEW events never touches the already-admitted past (append-only storage cannot
even express that rewrite).

The receiving side never re-signs and never grants standing: admission stores the origin's own
attestation verbatim. Receiving/gossiping an Event confers no authority (#69 §8 replica-stability).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from kawa.domain.events import Event, EventKind
from kawa.domain.credential import PublicKeyRegistry
from kawa.domain.ids import HLC
from kawa.domain.trust import TrustRegistry, admit, evaluate
from kawa.projections.reducers import _load_payload, reduce
from kawa.storage.emit import _insert_payload


@dataclass(frozen=True)
class Rejection:
    """One refused Event — reported, never silently dropped (event-log-and-replication §8 gap-detect)."""

    event_id: str
    origin_node: str
    origin_seq: int
    reason: str  # 'collision' | 'chain_gap' | 'chain_break' | 'envelope_invalid' | 'unsigned' |
    #              'provenance_invalid' | 'trust_rotated' | 'trust_revoked' | 'trust_unknown' |
    #              'forged_origin' | 'predecessor_rejected'


@dataclass(frozen=True)
class PullReport:
    admitted: list[str] = field(default_factory=list)   # event_ids, in apply order
    rejected: list[Rejection] = field(default_factory=list)


def frontier(conn: psycopg.Connection) -> dict[str, int]:
    """{origin_node -> highest origin_seq held}. Contiguous by construction: local emit is gap-free
    (per-origin lock) and admission only ever appends head+1, so max() IS the contiguous mark."""
    with conn.cursor() as cur:
        cur.execute("SELECT origin_node, max(origin_seq) FROM events GROUP BY origin_node")
        return {node: seq for node, seq in cur.fetchall()}


def read_stream(conn: psycopg.Connection, after: dict[str, int]) -> list[Event]:
    """Serve Events beyond a peer's frontier, per-origin ordered, WITH provenance columns.

    (reducers.load_events omits signature columns — replication must carry them, because the
    receiver's admission verifies the origin's attestation, not the server's word.)"""
    out: list[Event] = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_id, origin_node, origin_seq, hlc, kind, subject_ref, actor_ref, "
            "policy_digest, payload_digest, prev_hash, self_hash, "
            "signature, signing_key_ref, signature_scheme "
            "FROM events ORDER BY origin_node, origin_seq"
        )
        rows = cur.fetchall()
        for (eid, onode, oseq, hlc, kind, subj, actor, pol, pd, prev, sh, sig, kref, scheme) in rows:
            if oseq <= after.get(onode, 0):
                continue
            payload = _load_payload(cur, eid, kind)
            out.append(Event(
                event_id=eid, origin_node=onode, origin_seq=oseq, hlc=hlc,
                kind=EventKind(kind), subject_ref=str(subj) if subj else None,
                actor_ref=actor, policy_digest=pol, payload_digest=pd,
                prev_hash=prev, self_hash=sh,
                signature=sig, signing_key_ref=kref, signature_scheme=scheme,
                payload=payload,
            ))
    return out


def admit_batch(conn: psycopg.Connection, batch: list[Event], *, keys: PublicKeyRegistry,
                trust: TrustRegistry, clock: HLC | None = None) -> PullReport:
    """Trust-gated append of a received batch. Admitted Events are inserted (envelope + typed payload
    row, exactly as the origin emitted them) and reduced into the projections; every refusal is a
    typed Rejection. Once one Event of an origin stream is refused, the rest of that stream cannot
    chain and is rejected as 'predecessor_rejected' — contiguity is the completeness guarantee."""
    report_admitted: list[str] = []
    report_rejected: list[Rejection] = []
    heads: dict[str, tuple[int, str]] = {}   # origin -> (held seq, held self_hash)
    poisoned: set[str] = set()

    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (origin_node) origin_node, origin_seq, self_hash "
            "FROM events ORDER BY origin_node, origin_seq DESC"
        )
        for node, seq, sh in cur.fetchall():
            heads[node] = (seq, sh)

        for e in batch:
            def reject(reason: str) -> None:
                report_rejected.append(Rejection(e.event_id, e.origin_node, e.origin_seq, reason))
                poisoned.add(e.origin_node)

            if e.origin_node in poisoned:
                reject("predecessor_rejected")
                continue
            head_seq, head_hash = heads.get(e.origin_node, (0, None))
            if e.origin_seq <= head_seq:
                cur.execute("SELECT event_id FROM events WHERE origin_node=%s AND origin_seq=%s",
                            (e.origin_node, e.origin_seq))
                row = cur.fetchone()
                if row is not None and row[0] == e.event_id:
                    continue                   # already held — idempotent re-delivery is a no-op
                reject("collision")            # same position, different content: §4.1 halt+alert, never silent
                continue
            if e.origin_seq != head_seq + 1:
                reject("chain_gap")            # a hole in the gap-free stream — reported, not skipped
                continue
            if e.prev_hash != head_hash:
                reject("chain_break")          # does not chain onto what we hold
                continue
            if not e.verify():
                reject("envelope_invalid")     # content_hash / payload_digest mismatch
                continue
            # ---- the trust gate (the Phase 4C point) ----
            if e.signature is None or e.signing_key_ref is None or e.signature_scheme is None:
                reject("unsigned")             # cross-node requires attestation; local-only leniency ends here
                continue
            ev = evaluate(e.self_hash, e.signature, e.signing_key_ref, e.signature_scheme, keys, trust)
            if not admit(ev):
                reject("provenance_invalid" if not ev.provenance_valid else f"trust_{ev.trust_standing}")
                continue
            if trust.node_ref(e.signing_key_ref) != e.origin_node:
                reject("forged_origin")        # a trusted key may not speak as another node
                continue

            cur.execute(
                "INSERT INTO events (event_id, origin_node, origin_seq, hlc, kind, subject_ref, "
                "actor_ref, policy_digest, payload_digest, prev_hash, self_hash, "
                "signature, signing_key_ref, signature_scheme) "
                "VALUES (%s,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s)",
                (e.event_id, e.origin_node, e.origin_seq, e.hlc, e.kind.value, e.subject_ref,
                 e.actor_ref, e.policy_digest, e.payload_digest, e.prev_hash, e.self_hash,
                 e.signature, e.signing_key_ref, e.signature_scheme),
            )
            _insert_payload(cur, e)
            reduce(cur, e)
            heads[e.origin_node] = (e.origin_seq, e.self_hash)
            if clock is not None:
                clock.update(e.hlc)            # happens-before: our next emit orders after what we saw
            report_admitted.append(e.event_id)

    conn.commit()
    return PullReport(admitted=report_admitted, rejected=report_rejected)


def pull(dest: psycopg.Connection, source: psycopg.Connection, *, keys: PublicKeyRegistry,
         trust: TrustRegistry, clock: HLC | None = None) -> PullReport:
    """One anti-entropy pull (event-log-and-replication §4.1): frontier → missing events → gated admit.
    `keys`/`trust` are the RECEIVER's registries — trust is a local judgement, never taken from the peer."""
    return admit_batch(dest, read_stream(source, frontier(dest)), keys=keys, trust=trust, clock=clock)
