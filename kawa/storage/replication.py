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

from kawa.domain.events import AuthorityConfiguration, Event, EventKind
from kawa.domain.credential import PublicKeyRegistry
from kawa.domain.ids import HLC, scope_digest_of
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
    #              'equivocation' | 'restore_fork' | 'wire_invalid' (transport, replication_http) |
    #              'upgrade_digest_mismatch' (stub upgrade bytes != committed digest, 9a) |
    #              'scope_unrequested' (payload dropped, envelope admitted as stub, 9b) |
    #              'scope_digest_mismatch' (cleartext scope lies about the commitment, 9b)


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
                "envelope_version, scope_ref, scope_digest, materialized, "
                "signature, signing_key_ref, signature_scheme "
                "FROM events WHERE origin_node = %s AND origin_seq > %s ORDER BY origin_seq",
                (onode, lo),
            )
            rows = cur.fetchall()
            for (eid, onode, oseq, hlc, kind, subj, actor, pol, pd, prev, sh,
                 ever, sref, sdig, mat, sig, kref, scheme) in rows:
                payload = _load_payload(cur, eid, kind) if mat else None   # a held stub serves as a stub
                out.append(Event(
                    event_id=eid, origin_node=onode, origin_seq=oseq, hlc=hlc,
                    kind=EventKind(kind), subject_ref=str(subj) if subj else None,
                    actor_ref=actor, policy_digest=pol, payload_digest=pd,
                    prev_hash=prev, self_hash=sh,
                    envelope_version=ever, scope_ref=sref, scope_digest=sdig,
                    signature=sig, signing_key_ref=kref, signature_scheme=scheme,
                    payload=payload,
                ))
    return out


def held_stub_positions(conn: psycopg.Connection, scopes: tuple[str, ...]) -> dict[str, list[int]]:
    """The payload-backfill half of anti-entropy (#113 r2 BC-ii): the frontier is an
    ENVELOPE-level cursor — held stubs are counted, so a plain cursor pull never re-requests
    their bytes. This names the positions to backfill: held stubs whose `scope_digest`
    matches a scope this node is requesting (the receiver knows only digests for withheld
    events — but it knows which scopes it wants, and digests them)."""
    digests = {scope_digest_of(s) for s in scopes}
    out: dict[str, list[int]] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT origin_node, origin_seq FROM events "
                    "WHERE NOT materialized AND scope_digest = ANY(%s) "
                    "ORDER BY origin_node, origin_seq", (list(digests),))
        for onode, oseq in cur.fetchall():
            out.setdefault(onode, []).append(oseq)
    return out


def read_positions(conn: psycopg.Connection, positions: dict[str, list[int]]) -> list[Event]:
    """Serve specific held positions (the backfill request) — same shape as read_stream."""
    out: list[Event] = []
    with conn.cursor() as cur:
        for onode, seqs in sorted(positions.items()):
            for oseq in sorted(seqs):
                cur.execute(
                    "SELECT event_id, origin_node, origin_seq, hlc, kind, subject_ref, actor_ref, "
                    "policy_digest, payload_digest, prev_hash, self_hash, "
                    "envelope_version, scope_ref, scope_digest, materialized, "
                    "signature, signing_key_ref, signature_scheme "
                    "FROM events WHERE origin_node = %s AND origin_seq = %s", (onode, oseq),
                )
                row = cur.fetchone()
                if row is None:
                    continue
                (eid, onode2, oseq2, hlc, kind, subj, actor, pol, pd, prev, sh,
                 ever, sref, sdig, mat, sig, kref, scheme) = row
                payload = _load_payload(cur, eid, kind) if mat else None
                out.append(Event(
                    event_id=eid, origin_node=onode2, origin_seq=oseq2, hlc=hlc,
                    kind=EventKind(kind), subject_ref=str(subj) if subj else None,
                    actor_ref=actor, policy_digest=pol, payload_digest=pd,
                    prev_hash=prev, self_hash=sh,
                    envelope_version=ever, scope_ref=sref, scope_digest=sdig,
                    signature=sig, signing_key_ref=kref, signature_scheme=scheme,
                    payload=payload,
                ))
    return out


def withhold(e: Event) -> Event:
    """The serving-side redaction: the envelope without the payload bytes and without the
    cleartext scope — what a peer gets for an event it may not see."""
    if e.is_stub:
        return e
    return e.model_copy(update={"payload": None, "scope_ref": None})


def serve_batch(events: list[Event], grants: frozenset[str] | None = None) -> list[Event]:
    """The OFFER half of the grant algebra (#113 9b), applied to a stream about to cross the
    node boundary. v1 ships full (the legacy fleet-visible carve-out). A v2 event ships full
    ONLY when its scope is in the viewer's grants; unscoped v2 (no scope at all) is
    envelope-only to everyone, always (r2 BC-v: node-local materialization — no grant can
    name it). `grants=None` means an unknown viewer — withhold every v2 (fail closed)."""
    viewer = grants if grants is not None else frozenset()
    return [e if e.envelope_version < 2 or (e.scope_ref is not None and e.scope_ref in viewer)
            else withhold(e) for e in events]


def admit_batch(conn: psycopg.Connection, batch: list[Event], *, keys: PublicKeyRegistry,
                trust: TrustRegistry, clock: HLC | None = None,
                requested_scopes: frozenset[str] | None = None) -> PullReport:
    """Trust-gated append of a received batch. Admitted Events are inserted (envelope + typed payload
    row, exactly as the origin emitted them) and reduced into the projections; every refusal is a
    typed Rejection. Once one Event of an origin stream is refused, the rest of that stream cannot
    chain and is rejected as 'predecessor_rejected' — contiguity is the completeness guarantee.

    `requested_scopes` (#113 9b) is the RETAIN half of the grant algebra — receiver defense in
    depth: a v2 payload for a scope this pull did not request is dropped and its envelope admits
    as a stub (`scope_unrequested`, non-poisoning — a malicious or misconfigured server cannot
    push unauthorized content into this node's working set; the chain is never invalidated).
    None = no receiver scope policy (a local, trusted import — the transport callers always pass
    their requested set)."""
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
                cur.execute("SELECT event_id, self_hash, signing_key_ref, materialized FROM events "
                            "WHERE origin_node=%s AND origin_seq=%s",
                            (e.origin_node, e.origin_seq))
                row = cur.fetchone()
                if row is not None and row[0] == e.event_id:
                    # Re-delivery of a held event. BC-ii (#113 r2): if we hold only the STUB and
                    # the re-delivery carries the payload, this is the upgrade path — byte-verify
                    # against the held envelope's commitment, then materialize atomically. A
                    # silent `continue` here would drop the payload forever (the frontier already
                    # counts the stub, so anti-entropy would never re-request it).
                    held_materialized = row[3]
                    if not held_materialized and e.payload is not None:
                        if (requested_scopes is not None and e.envelope_version >= 2
                                and (e.scope_ref is None or e.scope_ref not in requested_scopes)):
                            # RETAIN check applies to upgrades too: an unrequested payload
                            # cannot materialize a held stub (non-poisoning; stub stays)
                            report_rejected.append(Rejection(
                                e.event_id, e.origin_node, e.origin_seq, "scope_unrequested"))
                        elif e.verify():       # re-derives payload_digest over the delivered bytes
                            cur.execute("UPDATE events SET materialized=true, scope_ref=%s "
                                        "WHERE event_id=%s", (e.scope_ref, e.event_id))
                            _insert_payload(cur, e)
                            reduce(cur, e)
                            report_admitted.append(e.event_id)
                        else:
                            # Same identity claim, bytes that do not hash to the commitment.
                            # Reported, never applied — and deliberately NOT origin-poisoning:
                            # the chain at the head is intact; only this upgrade claim is bad.
                            report_rejected.append(Rejection(
                                e.event_id, e.origin_node, e.origin_seq, "upgrade_digest_mismatch"))
                    continue                   # otherwise: idempotent re-delivery is a no-op
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
                    held_id, held_hash_at, held_key, _held_mat = row
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
            if e.scope_ref is not None and e.scope_digest != scope_digest_of(e.scope_ref):
                reject("scope_digest_mismatch")   # cleartext lies about the hashed commitment (9b)
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
            if (requested_scopes is not None and e.envelope_version >= 2 and not e.is_stub
                    and (e.scope_ref is None or e.scope_ref not in requested_scopes)):
                # RETAIN check (#113 9b): the payload was not asked for — drop it, admit the
                # envelope as a stub, report. Non-poisoning: the chain is intact, and the
                # position stays upgradeable if a grant+request later arrives.
                report_rejected.append(Rejection(
                    e.event_id, e.origin_node, e.origin_seq, "scope_unrequested"))
                e = withhold(e)

            cur.execute(
                "INSERT INTO events (event_id, origin_node, origin_seq, hlc, kind, subject_ref, "
                "actor_ref, policy_digest, payload_digest, prev_hash, self_hash, "
                "envelope_version, scope_ref, scope_digest, materialized, "
                "signature, signing_key_ref, signature_scheme) "
                "VALUES (%s,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (e.event_id, e.origin_node, e.origin_seq, e.hlc, e.kind.value, e.subject_ref,
                 e.actor_ref, e.policy_digest, e.payload_digest, e.prev_hash, e.self_hash,
                 e.envelope_version, e.scope_ref, e.scope_digest, not e.is_stub,
                 e.signature, e.signing_key_ref, e.signature_scheme),
            )
            if not e.is_stub:                  # the ONE materialization predicate: a stub admits
                _insert_payload(cur, e)        # envelope-only, advances the head, and is
                reduce(cur, e)                 # reducer-inert until upgraded (#113 9a)
                _authority_genesis_watch(cur, e, keys, report_rejected)
            heads[e.origin_node] = (e.origin_seq, e.self_hash)
            if clock is not None:
                clock.update(e.hlc)            # happens-before: our next emit orders after what we saw
            report_admitted.append(e.event_id)

    conn.commit()
    return PullReport(admitted=report_admitted, rejected=report_rejected)


def pull(dest: psycopg.Connection, source: psycopg.Connection, *, keys: PublicKeyRegistry,
         trust: TrustRegistry, clock: HLC | None = None,
         source_trust: TrustRegistry | None = None, puller_node: str | None = None,
         scopes: tuple[str, ...] | None = None) -> PullReport:
    """One anti-entropy pull (event-log-and-replication §4.1): frontier → missing events → gated admit.
    `keys`/`trust` are the RECEIVER's registries — trust is a local judgement, never taken from the peer.

    The grant algebra (#113 9b) composes here: the server OFFERS `granted ∩ requested`
    (`source_trust.scope_grants(puller_node)` ∩ `scopes`) — everything else crosses as a stub —
    and the receiver RETAINS only what it requested (`requested_scopes` in admission). Without a
    serving context (`source_trust`/`puller_node` None) every v2 event is withheld: least-visible
    is the fail-direction, never a default grant."""
    granted = (source_trust.scope_grants(puller_node)
               if source_trust is not None and puller_node is not None else frozenset())
    requested = frozenset(scopes) if scopes is not None else None
    offer = granted if requested is None else granted & requested
    stream = read_stream(source, frontier(dest))
    if scopes:   # payload backfill for held stubs in now-requested scopes (BC-ii)
        stream = stream + read_positions(source, held_stub_positions(dest, scopes))
    return admit_batch(dest, serve_batch(stream, offer),
                       keys=keys, trust=trust, clock=clock, requested_scopes=requested)


def _authority_genesis_watch(cur: psycopg.Cursor, e: Event, keys: PublicKeyRegistry,
                             report_rejected: list[Rejection]) -> None:
    """BC-1 at admission (#118 PR 3), reconciled with chain continuity: an authority
    genesis event is ADMITTED like any event (dropping a mid-chain event would break the
    origin's gap-free stream and grief the whole origin — a worse outcome than the one
    BC-1 exists to prevent), but its facial validity is judged HERE, loudly:

      - an unproven genesis (no founding-quorum signatures) is REPORTED as
        `authority_genesis_unproven` — it is admitted history that the verifier will
        never count (noise, per the PR-2 facially-valid rule); a stranger gains nothing.
      - a SECOND facially-valid genesis for a held key is the rev-2 (b) administrative
        conflict: recorded durably in `security_authority_conflict` and reported —
        BOTH lines stay blocked at verify time, no last-wins, no second live Cell.
    """
    from kawa.domain.authority import _facially_valid_genesis
    p = e.payload
    if not isinstance(p, AuthorityConfiguration) or p.prior_configuration_digest is not None:
        return
    if not _facially_valid_genesis(p, keys):
        report_rejected.append(Rejection(e.event_id, e.origin_node, e.origin_seq,
                                         "authority_genesis_unproven"))
        return
    cur.execute(
        "SELECT configuration_digest FROM event_authority_configuration "
        "WHERE authority_key=%s AND prior_configuration_digest IS NULL "
        "AND configuration_digest <> %s",
        (p.authority_key, p.configuration_digest))
    for (other,) in cur.fetchall():
        a, b = sorted([other, p.configuration_digest])
        cur.execute(
            "INSERT INTO security_authority_conflict (authority_key, digest_a, digest_b) "
            "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (p.authority_key, a, b))
        report_rejected.append(Rejection(e.event_id, e.origin_node, e.origin_seq,
                                         "authority_genesis_conflict"))


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


# resolve_fork moved to kawa/storage/authority_gate.py in step 10 (#118 10C, D1):
# fork-freeze release is a CP operation requiring a VALID AuthorityReceipt. The bare
# operator path was REMOVED — no receipt, no unfreeze, no fallback.
