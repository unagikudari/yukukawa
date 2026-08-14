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
    "envelope_version", "scope_ref", "scope_digest",
    "signature", "signing_key_ref", "signature_scheme",
)


class WireVerificationError(ValueError):
    """A transported event failed hash verification over its received bytes — fail closed."""


def to_wire(e: Event, *, as_stub: bool = False) -> dict:
    """One event as a wire dict: flat envelope + the payload as origin-canonical JSON text.

    `as_stub` (#113 9a): ship the envelope WITHOUT the payload bytes and WITHOUT the
    cleartext `scope_ref` — a withheld event reveals only its `scope_digest` commitment.
    An event that already IS a stub locally always ships as one (nothing to ship)."""
    env = {f: getattr(e, f) for f in _ENVELOPE_FIELDS}
    env["kind"] = e.kind.value
    if as_stub or e.is_stub:
        env["scope_ref"] = None
        return {"envelope": env, "payload_canonical": None}
    return {"envelope": env, "payload_canonical": canonical_json(e.payload.model_dump(mode="json"))}


def from_wire(obj: dict) -> Event:
    """Verify-before-parse (#111 rev 2 (b)).

    1. `payload_digest` is recomputed over the RECEIVED canonical text — the bytes, not a parse.
    2. `self_hash` is recomputed over the envelope + that digest and must equal `event_id`.
    3. Only then is the payload parsed into its typed model.
    Any mismatch is a `WireVerificationError`; the caller drops the event, never repairs it."""
    env = obj["envelope"]
    text = obj["payload_canonical"]
    version = env.get("envelope_version", 1)
    scope_digest = env.get("scope_digest")
    scope_ref = env.get("scope_ref")
    if version == 1 and (scope_digest is not None or scope_ref is not None):
        raise WireVerificationError("a v1 envelope never carries a scope (downgrade guard)")
    if text is not None:
        pd = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        if pd != env["payload_digest"]:
            raise WireVerificationError("payload_digest does not match received payload bytes")
    else:
        pd = env["payload_digest"]           # stub: the origin's commitment, verified at upgrade
    try:
        sh = event_hash(
            origin_node=env["origin_node"], origin_seq=env["origin_seq"], hlc=env["hlc"],
            kind=env["kind"], subject_ref=env["subject_ref"], actor_ref=env["actor_ref"],
            policy_digest=env["policy_digest"], payload_digest=pd, prev_hash=env["prev_hash"],
            envelope_version=version, scope_digest=scope_digest,
        )
    except ValueError as exc:                # unknown version: refuse, never guess
        raise WireVerificationError(str(exc)) from exc
    if sh != env["self_hash"] or sh != env["event_id"]:
        raise WireVerificationError("self_hash/event_id does not match received envelope")
    payload = _PAYLOAD.validate_python(json.loads(text)) if text is not None else None
    event = Event(
        event_id=env["event_id"], origin_node=env["origin_node"], origin_seq=env["origin_seq"],
        hlc=env["hlc"], kind=EventKind(env["kind"]), subject_ref=env["subject_ref"],
        actor_ref=env["actor_ref"], policy_digest=env["policy_digest"], payload_digest=pd,
        prev_hash=env["prev_hash"], self_hash=env["self_hash"],
        envelope_version=version, scope_ref=scope_ref, scope_digest=scope_digest,
        signature=env["signature"], signing_key_ref=env["signing_key_ref"],
        signature_scheme=env["signature_scheme"], payload=payload,
    )
    if scope_ref is not None and not event.verify():
        raise WireVerificationError("scope_ref does not match the hashed scope_digest")
    return event
