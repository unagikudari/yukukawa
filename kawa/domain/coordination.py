"""Wake/pull coordination (v0.5 §7/§7.1/§17) — the ghost-participant structural fix.

> Push for liveness. Pull for correctness. Wake the runtime, not the model context.

Two in-memory coordination primitives, both a trust/coordination plane — NOT Domain Events,
never authority (that is step 10), never exactly-once (that is step 8 effect identity):

- **WakeHint / WakeBus** — a wake is a non-authoritative HINT that Work may now be eligible.
  It carries only `{work_ref, reason}` (never instruction/record prose — the §7 anti-injection
  boundary), touches no projection and no claim, and its only effect is to prompt a
  participant to PULL. Pull (`work_next_for_participant`) is the sole authoritative path.
  Lost, duplicated, delayed, or reordered wakes never change authoritative Work state.

- **ClaimRegistry** — an advisory, single-store, dispatch-only exclusion so two *live*
  participants do not both pull the same Work. A claim carries no effect identity and no
  side-effect authority. It changes DISCOVERABILITY (a non-holder does not see a live-claimed
  Work), never READINESS (the Work stays `execution='ready'` and the global `work_next` still
  returns it). Expiry is lazy and evaluated at query time: `expires_at <= now` is treated as
  ABSENT *before* any filtering, so a stale/ghost claim can never suppress a contending
  participant's later pull — which is why no background reaper is needed for correctness.
  Reachability release is two distinct mechanisms: explicit `release_session` on an OBSERVED
  drop (`session_dropped`), and lease expiry on silence (`lease_expired`); Phase-0 has no
  silence detector, so §17's mechanical action holds only for observed drops.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

WakeReason = Literal["work_eligible", "dependency_satisfied", "rescan"]
ReleaseReason = Literal["completed", "session_dropped", "lease_expired", "released"]

DEFAULT_LEASE_SECONDS = 30.0


@dataclass(frozen=True)
class WakeHint:
    """A non-authoritative hint. Structurally cannot carry instruction/record prose — only a
    work_ref and a typed reason. The participant reacts by PULLING the full contract."""

    work_ref: str
    reason: WakeReason


class WakeBus:
    """In-process wake delivery. Emitting/draining touches NO Work state and NO claim — the
    bus is a mailbox of hints. `deferred` (a durable transport) is step-7-production; here a
    lost/duplicate/reordered hint is harmless by construction."""

    def __init__(self) -> None:
        self._boxes: dict[str, list[WakeHint]] = {}

    def subscribe(self, session_id: str) -> None:
        self._boxes.setdefault(session_id, [])

    def emit(self, session_id: str, hint: WakeHint) -> None:
        # a wake for an unsubscribed session is simply dropped — hints are best-effort
        if session_id in self._boxes:
            self._boxes[session_id].append(hint)

    def drain(self, session_id: str) -> list[WakeHint]:
        box = self._boxes.get(session_id, [])
        self._boxes[session_id] = []
        return list(box)


@dataclass(frozen=True)
class Claim:
    work_ref: str
    session_id: str
    claimed_at: float
    expires_at: float


class ClaimRegistry:
    """Advisory dispatch exclusion. `holder(work_ref, now)` is the ONE expiry rule: it returns
    None for an absent OR expired claim (and lazily prunes the expired row), so every consult
    of a claim goes through the same 'expired = absent' evaluation."""

    def __init__(self) -> None:
        self._claims: dict[str, Claim] = {}

    def holder(self, work_ref: str, now: float) -> str | None:
        """The current live holder's session_id, or None if unclaimed OR the lease expired.
        Lazily prunes an expired row (storage hygiene; correctness does not depend on it)."""
        c = self._claims.get(work_ref)
        if c is None:
            return None
        if c.expires_at <= now:                          # expired = ABSENT (the lazy rule)
            del self._claims[work_ref]
            return None
        return c.session_id

    def claim(self, work_ref: str, session_id: str, now: float,
              ttl: float = DEFAULT_LEASE_SECONDS) -> bool:
        """Acquire or refresh a claim. A DIFFERENT live holder blocks the claim (single live
        holder, presumed-live during the lease — slow vs dead is undecidable, so exclusion is
        the safe coordination choice). The same session may re-claim (heartbeat refresh):
        it extends the lease. Returns True on success."""
        h = self.holder(work_ref, now)
        if h is not None and h != session_id:
            return False                                 # held by another live participant
        self._claims[work_ref] = Claim(work_ref=work_ref, session_id=session_id,
                                       claimed_at=now, expires_at=now + ttl)
        return True

    def release(self, work_ref: str, session_id: str) -> bool:
        """Explicit release by the holder (e.g. Work completed). No-op if not held by this
        session. Returns True if a claim was removed."""
        c = self._claims.get(work_ref)
        if c is not None and c.session_id == session_id:
            del self._claims[work_ref]
            return True
        return False

    def release_session(self, session_id: str) -> list[str]:
        """Reachability release on an OBSERVED session drop (§17) — frees every claim held by
        that session immediately (reason=session_dropped). Returns the freed work_refs."""
        freed = [wr for wr, c in self._claims.items() if c.session_id == session_id]
        for wr in freed:
            del self._claims[wr]
        return freed
