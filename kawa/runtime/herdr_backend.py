"""Herdr runtime backend (#200 rev 3 §REAL-2) — the first RuntimeBackend.

Everything herdr-shaped stops here. Callers see only the contract's
vocabulary: opaque handle tokens, the presence/activity/attention triple,
and the closed `ERROR_CLASSES` set. Herdr's own words (socket paths, pane
and workspace ids, its lifecycle names, its stderr text) never ride outward
on an observation, a log line, or an exception — the backend catches
structured refusals at its edge and re-raises them classified.

Measured traps this backend is built around (H0 probe, #198 comment):

  * **T1 — named sessions are separate universes.** The session name selects
    the socket; the unflagged CLI reads the DEFAULT socket and reports
    "not running" even while the named session is healthy. `_argv` pins
    `--session` on EVERY invocation, and `detect()` separates "binary
    absent" from "server absent" instead of collapsing both into false.
  * **T2 — a prompt during REPL boot is swallowed** while still reporting
    success. Hence `launch()` deliberately does not gate, `await_settled()`
    exists, and cue delivery is verified by the caller through
    `read_recent_output` (the launcher owns that gate, #200 §REAL-3).

    Measured 2026-08-20 against herdr 0.8.0 (#204 step 3), because the plan
    required comparing the documented `agent prompt --wait` primitive with that
    gate rather than assuming either:

        kind    trials  --wait outcome            echo gate
        codex      4    ok (4)                    delivered (4)
        claude     6    timeout (5), stalled (1)  delivered (5), not (1)

    The five claude `timeout`s are FALSE ALARMS: the cue is demonstrably on the
    runtime's own screen in every one of them. That is not a defect in the
    primitive, it is a different question — `--wait` reports whether the agent
    reached a SETTLED state after submission, while the launcher needs to know
    whether the submission LANDED. The wake cue says "look", so an agent that
    received it correctly may work for far longer than any submission timeout;
    waiting for settlement therefore times out on a perfectly delivered cue.

    Two things weaken the RATE without touching that reasoning, and both were
    found by attacking this measurement rather than defending it:

      * An eleventh trial whose prompt asked for a reply ("<marker> reply with the
        single word ACK") returned `ok`, and both detectors agreed. So `--wait` is
        not uniformly wrong on claude; the bare-marker prompts of the first six may
        simply not have produced a state herdr recognises as settled.
      * The node was at 79% of its weekly claude limit during the measurement, which
        the runtime itself printed on screen. Throttling is an uncontrolled variable
        in those five timeouts and was not held constant.

    Neither rescues `--wait` as a delivery signal: slowness from ANY cause — real
    work, throttling, a TUI herdr cannot read — produces the same timeout on a
    submission that landed. That is the point, and it survives the confound. What
    does NOT survive is treating 5/6 as a measured failure rate; it is one node, one
    day, one quota state.

    So the echo gate stays primary, and the swap is declined explicitly rather
    than silently (#204 REAL-5). The one outcome that does carry delivery
    information MIGHT be `agent_prompt_stalled` — herdr observing no state change
    at all — which agreed with the gate on the single non-delivery seen.

    An earlier revision of this comment declined that signal on ARCHITECTURAL
    grounds ("it would put a herdr-specific signal in the launcher"). That
    reason was wrong, and a design review said so: the contract forbids herdr's
    vocabulary crossing the OUTER boundary, not this backend consuming it
    internally — `inspect()` already eats `agent_status` and `_run_json` already
    eats herdr's stderr, translating both into contract vocabulary. A
    `--wait --timeout` used only to shorten this backend's own retry timing,
    never surfacing "stalled" outward, would violate nothing.

    The reason it is not adopted here is EVIDENTIAL and smaller: the agreement
    is n=1. One occurrence is not a semantics, and the gate's retry loop already
    makes non-delivery recoverable rather than merely detected, so the fast-fail
    would buy latency, not correctness. That makes it a scoped follow-up under
    #204 — worth measuring deliberately, not worth inferring from one sample.
  * **T3 — the server owns the PTYs**, so stopping it kills the agents. This
    backend never starts or stops a server; a missing server is a named
    refusal, not something to fix behind the operator's back.

The handle token is an identifier this backend MINTS (`kawa-<digest>`) and
that herdr happens to accept as an agent name — so nothing herdr-shaped has
to be carried across the boundary to find a runtime again, and the token
carries no Work prose (round-2: derived from `work_ref` by digest, never
from an objective or title).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import time

from kawa.runtime.contract import (BackendStatus, LaunchSpec, RuntimeBackendError,
                                   RuntimeHandle, RuntimeObservation)
from kawa.runtime.env_policy import build_env

NAME = "herdr"

# validated against a live capture; below this the recorded response shapes
# are not known to hold (#200 rev 3: version pin + conformance smoke)
MIN_VERSION = (0, 8, 0)

_SETTLED = ("idle", "done", "blocked")

# herdr lifecycle -> (presence, activity, attention). ADAPTER-LEVEL POLICY:
# `done` is quiescent-with-attention because a finished turn wants a human
# or a supervisor to look — it is NEVER a statement about Work or Results.
_STATE_MAP = {
    "working": ("present", "active", "not_needed"),
    "blocked": ("present", "blocked", "needed"),
    "idle": ("present", "quiescent", "not_needed"),
    "done": ("present", "quiescent", "needed"),
    "unknown": ("present", "unknown", "unknown"),
}

# structured refusals seen in real captures -> our closed vocabulary
_ERROR_MAP = {
    "server_not_running": "server_absent",
    "agent_name_taken": "already_running",
    "timeout": "launch_timeout",
    "agent_pane_busy": "runtime_not_ready",
}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def token_for(work_ref: str) -> str:
    """Opaque, deterministic, prose-free handle token."""
    return "kawa-" + hashlib.sha256(work_ref.encode()).hexdigest()[:12]


class _AgentAbsent(Exception):
    """Internal: the runtime is simply not there (a state, not a failure)."""


class HerdrBackend:
    name = NAME

    def __init__(self, session: str, *, binary: str | None = None,
                 timeout_s: float = 20.0, pane_ready_s: float = 15.0) -> None:
        self.session = session
        self.timeout_s = timeout_s
        self.pane_ready_s = pane_ready_s
        self._binary = self._resolve_binary(binary)

    # ---- process plumbing (no shell, ever) ----

    @staticmethod
    def _resolve_binary(binary: str | None) -> str | None:
        """Pinned resolution (#200 §REAL-5): explicit path, else the pin in
        the environment, else PATH lookup — and the result must be an
        absolute path we can execute. No shell interpolation anywhere."""
        candidate = binary or os.environ.get("KAWA_HERDR_BIN") or shutil.which(NAME)
        if not candidate:
            return None
        candidate = os.path.abspath(candidate)
        return candidate if os.access(candidate, os.X_OK) else None

    def _argv(self, *args: str) -> list[str]:
        assert self._binary
        return [self._binary, "--session", self.session, *args]   # T1: always pinned

    def _run(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
        if not self._binary:
            raise RuntimeBackendError(NAME, "binary_absent")
        try:
            return subprocess.run(self._argv(*args), capture_output=True,
                                  timeout=timeout or self.timeout_s)
        except subprocess.TimeoutExpired:
            raise RuntimeBackendError(NAME, "launch_timeout", "cli did not return")
        except OSError:
            raise RuntimeBackendError(NAME, "binary_absent")

    def _run_json(self, *args: str, timeout: float | None = None) -> dict:
        proc = self._run(*args, timeout=timeout)
        # measured 2026-08-18: results arrive on stdout with rc=0, structured
        # refusals on STDERR with rc=1 — reading only stdout turns every real
        # refusal into "malformed" (found by the live conformance smoke; the
        # first captures had merged the streams and hidden the split)
        raw = proc.stdout if proc.stdout.strip() else proc.stderr
        try:
            payload = json.loads(raw or b"")
        except ValueError:
            # the refusal text itself is NOT forwarded: it carries socket
            # paths and pane ids, exactly the vocabulary the boundary excludes
            raise RuntimeBackendError(NAME, "malformed_response")
        if not isinstance(payload, dict):
            raise RuntimeBackendError(NAME, "malformed_response")
        if "error" in payload:
            self._classify_payload(payload)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeBackendError(NAME, "malformed_response")
        return result

    @staticmethod
    def _classify_payload(payload: dict) -> None:
        """Turn a structured refusal into our closed vocabulary. Always
        raises: `_AgentAbsent` for 'the runtime is simply not there', a typed
        adapter error otherwise — never the runtime's own wording."""
        code = str((payload.get("error") or {}).get("code", ""))
        if code == "agent_not_found":
            raise _AgentAbsent()
        raise RuntimeBackendError(NAME, _ERROR_MAP.get(code, "backend_refused"))

    def _classify(self, raw: bytes) -> None:
        """Same, for a stream we have not parsed yet. A body that is not a
        structured refusal returns quietly, leaving the caller to decide."""
        try:
            payload = json.loads(raw or b"")
        except ValueError:
            return
        if isinstance(payload, dict) and "error" in payload:
            self._classify_payload(payload)

    # ---- contract ----

    def detect(self) -> BackendStatus:
        """Honest three-way answer (T1): no binary / binary but no server /
        ready — never one collapsed boolean."""
        if not self._binary:
            return BackendStatus(False, "binary_absent")
        try:
            proc = subprocess.run([self._binary, "--version"], capture_output=True,
                                  timeout=self.timeout_s)
            version = (proc.stdout or b"").decode(errors="replace").split()[-1].strip()
            parts = tuple(int(p) for p in version.split(".")[:3])
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            return BackendStatus(False, "malformed_response")
        if parts < MIN_VERSION:
            return BackendStatus(False, "version_unsupported", version)
        try:
            self._run_json("workspace", "list")
        except _AgentAbsent:                       # cannot happen here; be total
            return BackendStatus(True, "", version)
        except RuntimeBackendError as exc:
            return BackendStatus(False, exc.error_class, version)
        return BackendStatus(True, "", version)

    def launch(self, spec: LaunchSpec) -> RuntimeHandle:
        """Create the workspace/pane and start the agent. Returns as soon as
        the runtime EXISTS — readiness is `await_settled`, so a runtime that
        wedges at a trust dialog is still addressable for cleanup."""
        token = token_for(spec.work_ref)
        env_args: list[str] = []
        for key, value in sorted(build_env().items()):
            env_args += ["--env", f"{key}={value}"]
        created = self._run_json("workspace", "create", "--label", token,
                                 "--cwd", spec.cwd, *env_args)
        pane = ((created.get("root_pane") or {}).get("pane_id"))
        if not isinstance(pane, str) or not pane:
            raise RuntimeBackendError(NAME, "malformed_response")
        try:
            self._start_when_shell_ready(token, spec.agent_kind, pane)
        except RuntimeBackendError as exc:
            # cleanup is best-effort ON PURPOSE — it must not replace the
            # error that explains WHY the launch failed. But a cleanup that
            # did not take leaves something behind, so it is named in the
            # detail rather than passed over in silence (round-2 review).
            if not self._close_pane(pane):
                raise RuntimeBackendError(NAME, exc.error_class,
                                          "cleanup incomplete") from exc
            raise
        return RuntimeHandle(NAME, token)

    def _start_when_shell_ready(self, token: str, kind: str, pane: str) -> None:
        """A freshly created pane refuses agents until its shell is up —
        measured as the `agent_pane_busy` refusal, which is transient and
        specific enough to retry on BY NAME (never a blanket retry, and
        never a blind sleep). Bounded: past the deadline it is a timeout."""
        deadline = time.monotonic() + self.pane_ready_s
        while True:
            try:
                self._run_json("agent", "start", token, "--kind", kind, "--pane", pane)
                return
            except RuntimeBackendError as exc:
                if exc.error_class != "runtime_not_ready" or time.monotonic() >= deadline:
                    raise RuntimeBackendError(NAME, "launch_timeout", "shell never became available") \
                        if exc.error_class == "runtime_not_ready" else exc
                time.sleep(0.25)

    def await_settled(self, handle: RuntimeHandle, timeout_s: float = 60.0) -> RuntimeObservation:
        args = ["agent", "wait", handle.token]
        for state in _SETTLED:
            args += ["--until", state]
        args += ["--timeout", str(int(timeout_s * 1000))]
        try:
            self._run_json(*args, timeout=timeout_s + self.timeout_s)
        except _AgentAbsent:
            return self._observation(handle, None)
        return self.inspect(handle)

    def wake(self, handle: RuntimeHandle, cue: str) -> None:
        """Deliver a wake cue. The backend transmits what it is given and
        judges nothing: the guarantee that only the fixed constant is ever
        passed lives in the caller's sealed path (#200 §REAL-3), which the
        boundary test verifies over the TOTAL bytes reaching the runtime."""
        try:
            self._run_json("agent", "prompt", handle.token, cue)
        except _AgentAbsent:
            raise RuntimeBackendError(NAME, "inspect_failed", "runtime absent")

    def inspect(self, handle: RuntimeHandle) -> RuntimeObservation:
        try:
            result = self._run_json("agent", "get", handle.token)
        except _AgentAbsent:
            return self._observation(handle, None)
        agent = result.get("agent")
        if not isinstance(agent, dict):
            raise RuntimeBackendError(NAME, "malformed_response")
        return self._observation(handle, agent.get("agent_status"))

    def read_recent_output(self, handle: RuntimeHandle) -> str:
        """Rendered terminal text. Secret-bearing telemetry by nature: the
        plan forbids promoting it into any durable store (#200 §REAL-4).

        An ABSENT runtime honestly has no output (empty string) — but any
        other refusal is classified and raised rather than flattened into
        emptiness, because the caller's wake gate reads emptiness as "the cue
        never landed" and would otherwise blame the agent for a transport
        failure it never saw."""
        proc = self._run("agent", "read", handle.token)
        if proc.returncode == 0:
            return (proc.stdout or b"").decode(errors="replace")
        try:
            self._classify(proc.stderr or proc.stdout)
        except _AgentAbsent:
            return ""
        raise RuntimeBackendError(NAME, "malformed_response")

    def terminate(self, handle: RuntimeHandle) -> None:
        """Idempotent: an already-absent runtime is success, not an error."""
        try:
            result = self._run_json("agent", "get", handle.token)
        except _AgentAbsent:
            return
        pane = (result.get("agent") or {}).get("pane_id")
        if not isinstance(pane, str) or not pane:
            raise RuntimeBackendError(NAME, "terminate_failed")
        self._close_pane(pane)
        try:
            self._run_json("agent", "get", handle.token)
        except _AgentAbsent:
            return
        raise RuntimeBackendError(NAME, "terminate_failed")

    # ---- internals ----

    def _close_pane(self, pane: str) -> bool:
        """Best-effort teardown; returns whether it was confirmed."""
        try:
            self._run_json("pane", "close", pane)
            return True
        except _AgentAbsent:
            return True                             # already gone is success
        except RuntimeBackendError:
            return False

    def _observation(self, handle: RuntimeHandle, state: str | None) -> RuntimeObservation:
        if state is None:
            presence, activity, attention = "absent", "unknown", "unknown"
        else:
            presence, activity, attention = _STATE_MAP.get(
                state, ("present", "unknown", "unknown"))
        return RuntimeObservation(backend=NAME, handle_token=handle.token,
                                  presence=presence, activity=activity,
                                  attention=attention, observed_at=_now())
