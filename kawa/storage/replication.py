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
    #              'forged_origin' | 'predecessor_rejected' | 'origin_frozen' |
    #              'equivocation' | 'restore_fork' | 'wire_invalid' (transport, replication_http)


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
    receiver's admission verifies the origin's attestation, not the server's word.)

    Indexed per-origin ranges (#111 8C): the frontier names exactly what each origin is missing,
    so serving is range queries over the (origin_node, origin_seq) natural key — never a full scan."""
    out: list[Event] = []
    with conn.cursor() as cur:
        for onode, top in sorted(frontier(conn).items()):
            lo = after.get(onode, 0)
            if top <= lo:
                continue
            cur.execute(
                "SELECT event_id, origin_node, origin_seq, hlc, kind, subject_ref, actor_ref, "
                "policy_digest, payload_digest, prev_hash, self_hash, "
                "signature, signing_key_ref, signature_scheme "
                "FROM events WHERE origin_node = %s AND origin_seq > %s ORDER BY origin_seq",
                (onode, lo),
            )
            rows = cur.fetchall()
            for (eid, onode, oseq, hlc, kind, subj, actor, pol, pd, prev, sh, sig, kref, scheme) in rows:
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
        # 8B: a frozen origin admits NOTHING until the operator resolves the fork — durable,
        # so the freeze holds across restarts and re-pulls (#111 acceptance).
        cur.execute("SELECT DISTINCT origin_node FROM security_fork_evidence WHERE frozen")
        frozen: set[str] = {r[0] for r in cur.fetchall()}

        for e in batch:
            def reject(reason: str) -> None:
                report_rejected.append(Rejection(e.event_id, e.origin_node, e.origin_seq, reason))
                poisoned.add(e.origin_node)

            if e.origin_node in frozen:
                reject("origin_frozen")
                continue
            if e.origin_node in poisoned:
                reject("predecessor_rejected")
                continue
            head_seq, head_hash = heads.get(e.origin_node, (0, None))
            if e.origin_seq <= head_seq:
                cur.execute("SELECT event_id, self_hash, signing_key_ref FROM events "
                            "WHERE origin_node=%s AND origin_seq=%s",
                            (e.origin_node, e.origin_seq))
                row = cur.fetchone()
                if row is not None and row[0] == e.event_id:
                    continue                   # already held — idempotent re-delivery is a no-op
                # A different event at a held position. Only a rival that would pass the FULL
                # trust gate at its CURRENT standing — provenance valid AND active AND correctly
                # attributed — is §13 fork evidence. Unauthenticated junk must stay a plain
                # collision (any stranger could freeze a healthy origin), and so must a rival
                # under a rotated/revoked key (PR #112 review finding 1: a distrusted key —
                # e.g. the loser of a resolved fork — could otherwise mint fresh rivals and
                # re-freeze the origin forever; current standing, not at_seq, for exactly that
                # reason). Never LWW either way: neither branch wins implicitly.
                if (row is not None and e.verify() and e.signature is not None
                        and e.signing_key_ref is not None and e.signature_scheme is not None
                        and admit(evaluate(e.self_hash, e.signature, e.signing_key_ref,
                                           e.signature_scheme, keys, trust))
                        and trust.node_ref(e.signing_key_ref) == e.origin_node):
                    held_id, held_hash_at, held_key = row
                    held_inc = trust.incarnation_ref(held_key) if held_key else None
                    rival_inc = trust.incarnation_ref(e.signing_key_ref)
                    # same known incarnation speaking with two voices = equivocation; distinct
                    # known incarnations = restore-fork. Unknown attribution classifies at the
                    # SEVERE end (equivocation) — fail toward scrutiny, never toward comfort.
                    classification = ("restore_fork"
                                      if held_inc is not None and rival_inc is not None
                                      and held_inc != rival_inc else "equivocation")
                    cur.execute(
                        "INSERT INTO security_fork_evidence (origin_node, origin_seq, "
                        "held_event_id, held_hash, rival_event_id, rival_hash, held_key_ref, "
                        "rival_key_ref, held_incarnation, rival_incarnation, classification) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (e.origin_node, e.origin_seq, held_id, held_hash_at, e.event_id,
                         e.self_hash, held_key, e.signing_key_ref, held_inc, rival_inc,
                         classification),
                    )
                    frozen.add(e.origin_node)
                    reject(classification)
                    continue
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
            # BC-3: judge trust AT the event's own seq — a fork-scoped revocation must not
            # reject legitimate pre-fork trunk events that replicate late.
            ev = evaluate(e.self_hash, e.signature, e.signing_key_ref, e.signature_scheme,
                          keys, trust, at_seq=e.origin_seq)
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


def incarnation_intervals(conn: psycopg.Connection, trust: TrustRegistry,
                          origin_node: str) -> list[tuple[str | None, int, int]]:
    """§13 "origin sequence within incarnation", made checkable from the trust plane (#111 8A):
    attribute each of an origin's events to the incarnation of its signing key and collapse runs
    into `(incarnation, lo_seq, hi_seq)` intervals in seq order. A healthy lineage yields each
    incarnation exactly ONE contiguous interval; an incarnation appearing twice (interleaved
    intervals) is succession evidence — surfaced by `check_incarnation_contiguity`."""
    with conn.cursor() as cur:
        cur.execute("SELECT origin_seq, signing_key_ref FROM events "
                    "WHERE origin_node=%s ORDER BY origin_seq", (origin_node,))
        rows = cur.fetchall()
    intervals: list[tuple[str | None, int, int]] = []
    for seq, key_ref in rows:
        inc = trust.incarnation_ref(key_ref) if key_ref else None
        if intervals and intervals[-1][0] == inc and intervals[-1][2] == seq - 1:
            intervals[-1] = (inc, intervals[-1][1], seq)
        else:
            intervals.append((inc, seq, seq))
    return intervals


def check_incarnation_contiguity(intervals: list[tuple[str | None, int, int]]) -> list[str]:
    """Violations: any incarnation owning more than one interval (its events interleave with
    another incarnation's — a succession that went backward or forked). Empty list = healthy."""
    seen: set[str | None] = set()
    violations: list[str] = []
    for inc, lo, hi in intervals:
        if inc in seen:
            violations.append(f"incarnation {inc!r} re-appears at seq {lo}..{hi} — "
                              "non-contiguous attribution (succession evidence)")
        seen.add(inc)
    return violations


def resolve_fork(conn: psycopg.Connection, trust: TrustRegistry, *, origin_node: str,
                 origin_seq: int, chosen_head: str, operator_ref: str, reason: str) -> None:
    """The ONLY release path for a frozen origin — an explicit, audited operator action (#111 8B).
    Forward-only: the losing branch's key is revoked scoped to the fork point (BC-3 — pre-fork
    trunk events that replicate late still verify), the resolution is recorded durably on the
    evidence row, and admission resumes for the chosen chain. Nothing here is automatic; no
    timeout, no retry count, no arrival order ever unfreezes an origin.

    Phase-0 boundary (named): `chosen_head` must be the HELD branch. Adopting a rival chain over
    an already-held tail is an authority-level history decision (append-only storage cannot drop
    the tail locally) — that machinery is step 10. Refused here, never silently attempted."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT held_event_id, rival_key_ref FROM security_fork_evidence "
            "WHERE origin_node=%s AND origin_seq=%s AND frozen",
            (origin_node, origin_seq),
        )
        rows = cur.fetchall()
        if not rows:
            raise ValueError(f"no frozen fork evidence at ({origin_node!r}, {origin_seq})")
        for held_event_id, rival_key_ref in rows:
            if chosen_head != held_event_id:
                raise ValueError(
                    "Phase-0 resolve_fork can only keep the held branch — adopting a rival chain "
                    "over held history is a step-10 authority decision (append-only storage "
                    "cannot rewrite the tail)"
                )
            if rival_key_ref is not None:
                trust.revoke(rival_key_ref, from_seq=origin_seq)   # BC-3: scoped, not total
        cur.execute(
            "UPDATE security_fork_evidence SET frozen=false, resolved_by=%s, "
            "resolved_at=clock_timestamp(), chosen_head=%s, reason=%s "
            "WHERE origin_node=%s AND origin_seq=%s AND frozen",
            (operator_ref, chosen_head, reason, origin_node, origin_seq),
        )
    conn.commit()
