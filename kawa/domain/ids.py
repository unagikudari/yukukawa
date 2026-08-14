"""Identity, ordering, and content-addressing primitives for Kawa.

These implement the mechanics the frozen contracts require:
  * UUIDv7 subject identity (subject-identity-and-lineage): time-ordered, immutable.
  * Hybrid Logical Clock (emit/replication): an ordering *hint*, never authority (③④ S6).
  * Content-addressed digests + per-origin hash chain (emit-enforcement).

Schema-first discipline (#57): these are pure functions over explicit inputs. No hidden
global clock authority — the HLC is an object you thread deliberately.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass


def uuid7(now_ms: int | None = None) -> uuid.UUID:
    """RFC 9562 UUIDv7 — 48-bit unix-ms timestamp + version/variant + randomness.

    Time-ordered so subject_refs sort by creation, without leaking a mutable clock into
    authority. `now_ms` is injectable for deterministic tests.
    """
    ts = now_ms if now_ms is not None else int(time.time() * 1000)
    ts &= (1 << 48) - 1
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF          # 12 bits
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)  # 62 bits
    value = (ts << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def canonical_json(obj: object) -> str:
    """Deterministic JSON for content-addressing: sorted keys, compact separators, UTF-8.

    Phase 0 approximation of JCS / RFC 8785 (③④ §6). Sufficient for local digests; the full
    JCS profile is a replaceable mechanic to adopt before cross-implementation equivalence.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj: object) -> str:
    """Content digest of a canonicalizable object: 'sha256:<hex>'."""
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def event_hash(
    *,
    origin_node: str,
    origin_seq: int,
    hlc: str,
    kind: str,
    subject_ref: str | None,
    actor_ref: str,
    policy_digest: str | None,
    payload_digest: str,
    prev_hash: str | None,
    envelope_version: int = 1,
    scope_digest: str | None = None,
) -> str:
    """Per-origin hash-chain link. self_hash binds the envelope + prev_hash, so any tamper
    or reordering within an origin's stream is detectable (emit-enforcement).

    THE single derivation for every path — emit VERIFY, admission, rebuild replay, and wire
    verification all come through here (#113 (a): no second implementation may exist).

    Versioned preimages (#113 rev 2, normative):
      v1  the original nine-field dict (sealed forever — pre-step-9 identities never change)
      v2  v1 fields + {"envelope_version": 2, "scope_digest": sha256-of-scope_ref-or-None}
    The version is INSIDE the v2 preimage, so a v2 envelope re-presented as v1 (or a v1 as
    v2) derives a different hash than its event_id — downgrade/upgrade forgery is a hash
    mismatch, not a policy check. A v1 envelope carrying a scope is structurally invalid
    and is rejected by the caller before hashing (Event.verify / wire)."""
    preimage: dict[str, object] = {
        "origin_node": origin_node,
        "origin_seq": origin_seq,
        "hlc": hlc,
        "kind": kind,
        "subject_ref": subject_ref,
        "actor_ref": actor_ref,
        "policy_digest": policy_digest,
        "payload_digest": payload_digest,
        "prev_hash": prev_hash,
    }
    if envelope_version == 1:
        return digest(preimage)
    if envelope_version == 2:
        preimage["envelope_version"] = 2
        preimage["scope_digest"] = scope_digest
        return digest(preimage)
    raise ValueError(f"unknown envelope_version {envelope_version!r} — refuse, never guess")


def scope_digest_of(scope_ref: str) -> str:
    """The pseudonymous per-scope marker committed by the v2 preimage (#113 rev 2 OQ3):
    stable so filtering works, digest so stubs need not name the scope. Dictionary attacks
    on guessable scope names are possible and stated; salting is envelope-v3 territory."""
    return "sha256:" + hashlib.sha256(scope_ref.encode("utf-8")).hexdigest()


@dataclass
class HLC:
    """Hybrid Logical Clock. Ordering hint only — a higher HLC never *creates* authority (S6).

    Format: "<physical_ms>.<logical>.<node>". `tick` advances on local emit; `update` merges
    a received clock. `_now_ms` is injectable for deterministic tests.
    """

    node: str
    physical_ms: int = 0
    logical: int = 0

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def tick(self, now_ms: int | None = None) -> str:
        now = now_ms if now_ms is not None else self._now_ms()
        if now > self.physical_ms:
            self.physical_ms, self.logical = now, 0
        else:
            self.logical += 1
        return self.stamp()

    def update(self, other: str, now_ms: int | None = None) -> str:
        o_phys, o_log, _ = other.split(".", 2)
        now = now_ms if now_ms is not None else self._now_ms()
        peak = max(now, self.physical_ms, int(o_phys))
        if peak == self.physical_ms == int(o_phys):
            self.logical = max(self.logical, int(o_log)) + 1
        elif peak == self.physical_ms:
            self.logical += 1
        elif peak == int(o_phys):
            self.logical = int(o_log) + 1
        else:
            self.logical = 0
        self.physical_ms = peak
        return self.stamp()

    def stamp(self) -> str:
        return f"{self.physical_ms}.{self.logical}.{self.node}"
