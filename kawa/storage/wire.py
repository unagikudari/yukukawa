"""Byte-preserving wire format for replicated Events (#111 8C).

The canonicalization rule that reproduces `self_hash` across nodes is: **don't re-canonicalize.**
The server ships each event's payload as the origin-canonical JSON string (`canonical_json` — the
same function every hash was computed with); the receiver verifies `payload_digest` and `self_hash`
over the RECEIVED bytes *before* parsing anything. JSON number rendering, Unicode normalization,
and key order therefore cannot drift on the wire by construction — a transported byte difference
is a verification failure, never a silent re-interpretation. (The cross-implementation JCS profile
remains ids.py's named debt; this module only promises Python↔Python byte fidelity.)
"""
from __future__ import annotations

import hashlib
import json

from pydantic import TypeAdapter

from kawa.domain.events import Event, EventKind, Payload
from kawa.domain.ids import canonical_json, event_hash

_PAYLOAD = TypeAdapter(Payload)   # discriminates on the payload's literal `kind`

_ENVELOPE_FIELDS = (
    "event_id", "origin_node", "origin_seq", "hlc", "kind", "subject_ref", "actor_ref",
    "policy_digest", "payload_digest", "prev_hash", "self_hash",
    "signature", "signing_key_ref", "signature_scheme",
)


class WireVerificationError(ValueError):
    """A transported event failed hash verification over its received bytes — fail closed."""


def to_wire(e: Event) -> dict:
    """One event as a wire dict: flat envelope + the payload as origin-canonical JSON text."""
    env = {f: getattr(e, f) for f in _ENVELOPE_FIELDS}
    env["kind"] = e.kind.value
    return {"envelope": env, "payload_canonical": canonical_json(e.payload.model_dump(mode="json"))}


def from_wire(obj: dict) -> Event:
    """Verify-before-parse (#111 rev 2 (b)).

    1. `payload_digest` is recomputed over the RECEIVED canonical text — the bytes, not a parse.
    2. `self_hash` is recomputed over the envelope + that digest and must equal `event_id`.
    3. Only then is the payload parsed into its typed model.
    Any mismatch is a `WireVerificationError`; the caller drops the event, never repairs it."""
    env = obj["envelope"]
    text = obj["payload_canonical"]
    pd = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    if pd != env["payload_digest"]:
        raise WireVerificationError("payload_digest does not match received payload bytes")
    sh = event_hash(
        origin_node=env["origin_node"], origin_seq=env["origin_seq"], hlc=env["hlc"],
        kind=env["kind"], subject_ref=env["subject_ref"], actor_ref=env["actor_ref"],
        policy_digest=env["policy_digest"], payload_digest=pd, prev_hash=env["prev_hash"],
    )
    if sh != env["self_hash"] or sh != env["event_id"]:
        raise WireVerificationError("self_hash/event_id does not match received envelope")
    payload = _PAYLOAD.validate_python(json.loads(text))
    return Event(
        event_id=env["event_id"], origin_node=env["origin_node"], origin_seq=env["origin_seq"],
        hlc=env["hlc"], kind=EventKind(env["kind"]), subject_ref=env["subject_ref"],
        actor_ref=env["actor_ref"], policy_digest=env["policy_digest"], payload_digest=pd,
        prev_hash=env["prev_hash"], self_hash=env["self_hash"], signature=env["signature"],
        signing_key_ref=env["signing_key_ref"], signature_scheme=env["signature_scheme"],
        payload=payload,
    )
