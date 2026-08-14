"""Segment commitments and the archive shape (#113 9c — v0.5 §12.3).

A **segment commitment** is a verification-and-custody attestation, NOT a re-proof of
authorship (each event already carries the origin's signature): the archiver attests that
it held and verified a CONTIGUOUS per-origin range under a named policy —

    commitment = { origin_node, from_seq, from_hash, to_seq, to_hash,
                   event_set_digest,           -- digest over the ordered event_ids
                   policy, attested_at }
    signed by the archiver's node key over digest(commitment)

which is exactly what a light node needs in order to trust an archive it cannot rebuild:
contiguity-at-a-range, custody, and a re-checkable file identity.

Verification is layered, and each layer fails separately (tamper localization):
  1. the archiver's signature over the commitment
  2. the event-set digest (membership + order of the file's events)
  3. the boundary hashes and gap-free contiguity of the range
  4. every event's own wire verification (byte digests, envelope hashes)

**Detached segments stay OUTSIDE the live frontier**: `archive_import` verifies and records
custody evidence in the security plane (`security_archive_segment`), then offers the events
to NORMAL admission — they enter the live store only if they chain onto held history
(gap-fill); otherwise the segment remains verified, detached evidence. Anti-entropy never
counts it. There is deliberately NO pruning path anywhere in this module (§11: retrieval
lifecycle ≠ retention; export never deletes).
"""
from __future__ import annotations

import json
import os

import psycopg

from kawa.domain.credential import NodeCredential, PublicKeyRegistry, resolve_and_verify_provenance
from kawa.domain.events import Event
from kawa.domain.ids import HLC, digest
from kawa.domain.trust import TrustRegistry
from kawa.storage.replication import PullReport, admit_batch, read_positions
from kawa.storage.wire import WireVerificationError, from_wire, to_wire

ARCHIVE_POLICY = "chain+envelope+bytes-v1"


class ArchiveVerificationError(ValueError):
    """One failed verification layer — named, so tampering localizes."""


def _commitment(events: list[Event], *, policy: str, attested_at: str) -> dict:
    first, last = events[0], events[-1]
    return {
        "origin_node": first.origin_node,
        "from_seq": first.origin_seq,
        "from_hash": first.prev_hash,
        "to_seq": last.origin_seq,
        "to_hash": last.self_hash,
        "event_set_digest": digest([e.event_id for e in events]),
        "policy": policy,
        "attested_at": attested_at,
    }


def archive_export(conn: psycopg.Connection, *, origin_node: str, from_seq: int, to_seq: int,
                   path: str, credential: NodeCredential, attested_at: str) -> str:
    """Write one origin range as a self-verifying archive file; returns the commitment digest.
    Export READS ONLY — it never deletes, truncates, or marks anything (§11)."""
    events = read_positions(conn, {origin_node: list(range(from_seq, to_seq + 1))})
    if len(events) != to_seq - from_seq + 1 or any(e.is_stub for e in events):
        raise ArchiveVerificationError("export range is not fully materialized on this node")
    commitment = _commitment(events, policy=ARCHIVE_POLICY, attested_at=attested_at)
    cd = digest(commitment)
    doc = {
        "commitment": commitment,
        "commitment_digest": cd,
        "signature": credential.sign(cd),
        "signing_key_ref": credential.signing_key_ref,
        "signature_scheme": credential.signature_scheme,
        "events": [to_wire(e) for e in events],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return cd


def verify_archive_file(path: str, *, keys: PublicKeyRegistry) -> tuple[list[Event], dict]:
    """The four layers, in order; raises ArchiveVerificationError naming the failed layer."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    commitment = doc["commitment"]
    cd = digest(commitment)
    if cd != doc.get("commitment_digest"):
        raise ArchiveVerificationError("commitment digest mismatch")
    if not resolve_and_verify_provenance(cd, doc["signature"], doc["signing_key_ref"],
                                         doc["signature_scheme"], keys):
        raise ArchiveVerificationError("archiver signature invalid")           # layer 1
    try:
        events = [from_wire(obj) for obj in doc["events"]]                     # layer 4 per event
    except WireVerificationError as exc:
        raise ArchiveVerificationError(f"event byte verification failed: {exc}") from exc
    if any(e.is_stub for e in events):
        # mirror of the export guard (#117 review): the policy attests BYTES; a file smuggling
        # payload-less stubs under a bytes policy is not a valid archive of anything
        raise ArchiveVerificationError("archive contains stubs — policy attests bytes")
    if digest([e.event_id for e in events]) != commitment["event_set_digest"]:
        raise ArchiveVerificationError("event-set digest mismatch")            # layer 2
    if not events:
        raise ArchiveVerificationError("empty segment")
    first, last = events[0], events[-1]
    contiguous = all(b.origin_seq == a.origin_seq + 1 and b.prev_hash == a.self_hash
                     and b.origin_node == a.origin_node
                     for a, b in zip(events, events[1:]))
    if (not contiguous or first.origin_node != commitment["origin_node"]
            or first.origin_seq != commitment["from_seq"]
            or first.prev_hash != commitment["from_hash"]
            or last.origin_seq != commitment["to_seq"]
            or last.self_hash != commitment["to_hash"]):
        raise ArchiveVerificationError("boundary/contiguity mismatch")         # layer 3
    return events, doc


def archive_import(conn: psycopg.Connection, path: str, *, keys: PublicKeyRegistry,
                   trust: TrustRegistry, clock: HLC | None = None) -> PullReport:
    """Verify custody evidence, record it, and OFFER the segment to normal admission.

    The evidence row never touches the frontier; the events land in the live store only if
    they chain onto held history (the ordinary gap-fill semantics of `admit_batch`). A
    segment that does not reach the held head stays `detached=true` — verified archive
    evidence a node may hold about history it has not (yet) materialized."""
    events, doc = verify_archive_file(path, keys=keys)
    report = admit_batch(conn, events, keys=keys, trust=trust, clock=clock)
    c = doc["commitment"]
    # `detached` is a fact about the STORE, never about this call's admission count — an
    # idempotent re-import of an already-chained segment admits nothing, and must not
    # regress the evidence to detached (the segment head either sits chained in the live
    # log, or it does not).
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM events WHERE origin_node=%s AND origin_seq=%s AND self_hash=%s",
                    (c["origin_node"], c["to_seq"], c["to_hash"]))
        detached = cur.fetchone() is None
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_archive_segment (commitment_digest, origin_node, from_seq, "
            "to_seq, from_hash, to_hash, event_set_digest, archiver_key_ref, path, detached) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (commitment_digest) DO UPDATE SET detached = EXCLUDED.detached, "
            "verified_at = clock_timestamp()",
            (doc["commitment_digest"], c["origin_node"], c["from_seq"], c["to_seq"],
             c["from_hash"], c["to_hash"], c["event_set_digest"], doc["signing_key_ref"],
             os.path.abspath(path), detached),
        )
    conn.commit()
    return report
