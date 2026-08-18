"""RuntimeBackend contract (#200 rev 3 §REAL-1) — the adapter boundary.

Everything importable from this module is backend-NEUTRAL by construction:

  * `RuntimeObservation` serializes through an explicit field allowlist —
    the serialized form (status bytes, log lines) is the boundary the plan
    guarantees over, and its vocabulary is fixed here, not by any backend.
  * `RuntimeHandle` is opaque: `token` is a backend-private payload that
    nothing outside the owning backend may parse (a test enforces that no
    backend vocabulary appears in serialized observations or in exception
    messages — round-1 F1 / round-2: exceptions are part of the boundary).
  * `RuntimeBackendError` messages are built ONLY from the named error-class
    vocabulary below plus the backend enum tag; raw CLI stderr never rides
    an exception upward.

Herdr's own lifecycle words never cross this line; the #198 triple
(presence/activity/attention) is the only state vocabulary that exists
above it. `attention=needed` is runtime telemetry — it never means, causes,
or completes any Kawa Work/Result standing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

PRESENCE = ("present", "absent", "unknown")
ACTIVITY = ("active", "quiescent", "blocked", "unknown")
ATTENTION = ("needed", "not_needed", "unknown")

# the complete, closed error vocabulary an adapter may surface (#200 rev 3):
ERROR_CLASSES = (
    "binary_absent",       # runtime binary not installed / not at pinned path
    "server_absent",       # binary present, resident server not running (T1)
    "version_unsupported", # live version below the validated pin
    "already_running",     # a runtime already exists for this handle token
    "runtime_not_ready",   # transient: the runtime host is not yet accepting agents
    "blocked_at_launch",   # agent stopped at a trust/permission dialog (H0 T2 family)
    "wake_echo_missing",   # cue submitted but never observed in output (T2)
    "launch_timeout",      # bounded launch/wake deadline exceeded
    "inspect_failed",      # backend could not answer liveness — caller must fail safe
    "terminate_failed",    # cleanup did not confirm
    "malformed_response",  # backend spoke, but not in its documented format
    "backend_refused",     # any other structured refusal, vocabulary NOT forwarded
)

# serialized allowlist — the ONLY keys a RuntimeObservation may emit
OBSERVATION_FIELDS = ("backend", "handle_token", "presence", "activity",
                      "attention", "observed_at")


class RuntimeBackendError(Exception):
    """Typed adapter failure. `error_class` is from ERROR_CLASSES; the
    message is exactly `<backend>:<error_class>` plus an optional NEUTRAL
    detail composed by the adapter (never raw subprocess output)."""

    def __init__(self, backend: str, error_class: str, detail: str = ""):
        assert error_class in ERROR_CLASSES, error_class
        self.backend, self.error_class, self.detail = backend, error_class, detail
        super().__init__(f"{backend}:{error_class}" + (f" ({detail})" if detail else ""))


@dataclass(frozen=True)
class RuntimeHandle:
    """Opaque runtime reference. `token` belongs to the backend that minted
    it; everyone else treats it as an identity-free string."""
    backend: str
    token: str


@dataclass(frozen=True)
class RuntimeObservation:
    """One inspection result in the #198 triple. Serialization is allowlisted
    — `to_dict` is the ONLY sanctioned serialized form."""
    backend: str
    handle_token: str
    presence: str
    activity: str
    attention: str
    observed_at: str            # ISO-8601 UTC

    def __post_init__(self) -> None:
        assert self.presence in PRESENCE, self.presence
        assert self.activity in ACTIVITY, self.activity
        assert self.attention in ATTENTION, self.attention

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in OBSERVATION_FIELDS}


@dataclass(frozen=True)
class LaunchSpec:
    """What a launch needs — note there is deliberately NO field for prompt
    text, objective, or title: Work prose cannot even be expressed here
    (sealed wake path, #200 rev 3 §REAL-3). work_ref is used for derived
    IDENTIFIERS only (hashed labels), never rendered as prose."""
    work_ref: str
    agent_kind: str             # 'claude' | 'codex' (H1-verified kinds)
    cwd: str


@dataclass(frozen=True)
class BackendStatus:
    available: bool
    reason: str                 # '' when available, else an ERROR_CLASSES name
    version: str | None = None


class RuntimeBackend(Protocol):
    """The replaceable-mechanics surface (#198). Implementations live in
    sibling modules; the Core never imports those directly.

    What a backend must GUARANTEE for the contract to be portable — stated
    because "replaceable" is a claim H3 has to be able to prove with a
    second, unrelated backend (round-2 review asked what here is secretly
    shaped like one runtime):

      * **Addressable handles.** A backend must be able to find a runtime it
        created again, given only the handle token. If its runtime cannot be
        addressed by a name the backend chooses, the backend owns the
        mapping and persists it — the token stays opaque either way.
      * **Settlement is observable.** Every runtime has a transient starting
        phase and a point past it; `await_settled` reports the state at that
        point. A backend that genuinely cannot distinguish the two must say
        `activity="unknown"` rather than claim a settled state it did not
        observe.
      * **Absence is distinguishable from failure.** "The runtime is not
        there" is `presence="absent"`; a backend that could not answer
        raises `inspect_failed`. Collapsing the two lets infrastructure
        failure be misread as an agent that finished.
      * **Cleanup is idempotent.** `terminate` on an already-absent runtime
        is success, and a partial teardown says so through `terminate_failed`
        rather than reporting a clean exit it did not achieve.

    Nothing above names a mechanism: a tmux backend satisfies it with window
    names, a direct-process backend with pidfiles."""

    name: str

    def detect(self) -> BackendStatus: ...

    def launch(self, spec: LaunchSpec) -> RuntimeHandle:
        """Make the runtime EXIST and return its handle. Deliberately does
        NOT gate on readiness: the caller must always receive a handle it can
        terminate, or a wedged runtime would be unreachable for cleanup
        (round-2). Gating is `await_settled` + the caller's policy."""

    def await_settled(self, handle: RuntimeHandle, timeout_s: float) -> RuntimeObservation:
        """Block until the runtime leaves its transient/boot phase, then
        report. Raises `launch_timeout` on deadline. Backend-neutral: every
        runtime has 'still starting' vs 'settled'."""

    def wake(self, handle: RuntimeHandle, cue: str) -> None: ...
    def inspect(self, handle: RuntimeHandle) -> RuntimeObservation: ...
    def read_recent_output(self, handle: RuntimeHandle) -> str: ...
    def terminate(self, handle: RuntimeHandle) -> None: ...
