"""tmux runtime backend (#204 step 2) — the second RuntimeBackend.

#198's thesis is "stable semantics, replaceable mechanics". One adapter cannot
demonstrate that; a `FakeBackend` in the tests demonstrates it even less, because a
fake is written by the same hand that wrote the contract and will agree with it by
construction. tmux is chosen because the fleet already runs agents in it, so the
comparison is honest rather than a toy.

## What tmux cannot see, and why that is the point

tmux knows a window exists and can show what it printed. It cannot tell **working**
from **blocked at a prompt** — both are a process holding a PTY with nothing new on
screen. The contract's answer is not to guess:

    A backend that genuinely cannot distinguish the two must say `activity="unknown"`
    rather than claim a settled state it did not observe.

So this backend reports `activity="unknown"` always, and never `attention`. The
consequence is concrete and belongs in the open rather than in a docstring nobody
reads: **the launcher's `blocked_at_launch` gate can never fire for a tmux runtime.**
That gate trips on `activity == "blocked"`, which this backend will never report.

That is not a hole this backend papers over. The launcher has a second, independent
wedge detector — the wake cue must appear in the runtime's own output, or the launch
is `wake_echo_missing` — and that one works on evidence tmux CAN produce. For tmux it
is the only wedge detector, and an operator should know that rather than assume two.

#204 says discovering that a gate is herdr-shaped would be a success of the plan. The
finding is milder than that: the gates hold, but they do not hold EQUALLY. One of the
two is load-bearing for herdr and decorative for tmux.

## Measured tmux behaviour this is built around (2026-08-19, tmux 3.6)

  * **Window targets prefix-match.** `-t "sess:kawa-abc"` resolves to a window named
    `kawa-abcLONG` when no exact `kawa-abc` exists — measured. Every target here uses
    the documented `=` exact-match prefix, so an operation can never land on a
    neighbouring runtime. Handle tokens are fixed-length digests and cannot collide
    by prefix anyway; `=` makes that structural rather than incidental.
  * **Killing an absent window exits 1** with `can't find window: <name>`. That is
    ABSENCE, not failure — the contract requires `terminate` on an already-absent
    runtime to be success, and requires a clean return to MEAN absence (#210). So a
    kill is always followed by a confirming `inspect`, and an unconfirmed teardown
    raises `terminate_failed` rather than returning and being believed.
  * **A session is not a server.** tmux starts its server implicitly, so "the server
    is down" has no tmux analogue. The named SESSION is the operator-owned container,
    and this backend never creates one: #204 keeps runtime-host lifecycle with the
    operator, and a backend that silently conjures its own session would make
    "attached to the fleet's tmux" and "invented a private one" indistinguishable.
"""
from __future__ import annotations

import datetime
import hashlib
import shutil
import subprocess
import time

from kawa.runtime.contract import (BackendStatus, LaunchSpec, RuntimeBackendError,
                                   RuntimeHandle, RuntimeObservation)
from kawa.runtime.env_policy import build_env

NAME = "tmux"

# The agent to run in the window. tmux, unlike herdr, has no notion of an "agent
# kind" — it runs a command — so the mapping from the contract's vocabulary to a
# command line lives HERE, at the boundary, and nowhere else.
_COMMAND = {"claude": "claude", "codex": "codex"}

_QUIET_S = 1.5          # output unchanged for this long = past the boot phase
_POLL_S = 0.25


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def token_for(work_ref: str) -> str:
    """Opaque, deterministic, prose-free handle token — the same shape the herdr
    backend mints, because it is a property of the CONTRACT (a token carries no Work
    prose) rather than of either runtime."""
    return "kawa-" + hashlib.sha256(work_ref.encode()).hexdigest()[:12]


class TmuxBackend:
    name = NAME

    def __init__(self, session: str, *, binary: str | None = None,
                 timeout_s: float = 20.0):
        self.session = session
        self.timeout_s = timeout_s
        self._binary = binary or shutil.which("tmux")

    # ---- plumbing -------------------------------------------------------------

    def _target(self, token: str) -> str:
        """Exact-match target, on BOTH components. The `=` is not decoration: without
        it tmux prefix-matches, and an operation meant for one runtime lands on
        another.

        The session half is the one I got wrong first, having fixed only the window
        half — measured, `list-windows -t kawa` returns the windows of a session named
        `kawa-fleet` when no session named `kawa` exists. A backend configured for a
        session that does not exist would then list, launch into, and KILL WINDOWS IN
        a neighbouring session that does. `detect()` did not catch it: it already used
        `=` and correctly reported `server_absent`, so the hazard lived entirely in the
        paths that run afterwards (#226 review round 1, finding 1)."""
        return f"={self.session}:={token}"

    def _run(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
        if not self._binary:
            raise RuntimeBackendError(NAME, "binary_absent")
        try:
            return subprocess.run([self._binary, *args], capture_output=True,
                                  timeout=timeout or self.timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeBackendError(NAME, "launch_timeout", "tmux did not return") from exc
        except OSError as exc:
            raise RuntimeBackendError(NAME, "binary_absent") from exc

    def _windows(self) -> list[str]:
        """Window names in the session. Raises `inspect_failed` when tmux cannot
        answer — the contract forbids collapsing "could not tell" into "absent",
        because that lets infrastructure failure read as an agent that finished."""
        proc = self._run("list-windows", "-t", f"={self.session}", "-F", "#{window_name}")
        if proc.returncode != 0:
            raise RuntimeBackendError(NAME, "inspect_failed", "session not readable")
        return (proc.stdout or b"").decode(errors="replace").split()

    # ---- contract -------------------------------------------------------------

    def detect(self) -> BackendStatus:
        """Three-way, like herdr's: no binary / binary but no session / ready.

        The session is the operator-owned container. Reporting `server_absent` when
        it does not exist rather than creating it keeps runtime-host lifecycle with
        the operator (#204 REAL-6) — and keeps "attached to the fleet's tmux" from
        being confused with "invented a private one"."""
        if not self._binary:
            return BackendStatus(False, "binary_absent")
        proc = self._run("-V")
        version = (proc.stdout or b"").decode(errors="replace").strip()
        has = self._run("has-session", "-t", f"={self.session}")
        if has.returncode != 0:
            return BackendStatus(False, "server_absent", version)
        return BackendStatus(True, "", version)

    def launch(self, spec: LaunchSpec) -> RuntimeHandle:
        """Create the window and start the agent in it. Returns as soon as the
        runtime EXISTS — readiness is `await_settled`, so a runtime that wedges is
        still addressable for cleanup."""
        command = _COMMAND.get(spec.agent_kind)
        if command is None:
            raise RuntimeBackendError(NAME, "backend_refused", "unsupported agent kind")
        token = token_for(spec.work_ref)
        if token in self._windows():
            raise RuntimeBackendError(NAME, "already_running")

        env_args: list[str] = []
        for key, value in sorted(build_env().items()):
            env_args += ["-e", f"{key}={value}"]
        # `-t "={session}:"` — the trailing colon is load-bearing and was missing from
        # the first fix. `new-window -t` parses its argument as a target WINDOW and only
        # falls back to a session, and that fallback loses the exact match: measured,
        # `-t "=kawa-x"` created the window inside a neighbouring `kawa-x-fleet` while
        # `-t "=kawa-x:"` correctly said "can't find session". Applying `=` without the
        # colon looked like the fix and was not one.
        proc = self._run("new-window", "-d", "-t", f"={self.session}:", "-n", token,
                         "-c", spec.cwd, *env_args, command)
        if proc.returncode != 0:
            raise RuntimeBackendError(NAME, "backend_refused", "window not created")
        return RuntimeHandle(NAME, token)

    def await_settled(self, handle: RuntimeHandle, timeout_s: float = 60.0) -> RuntimeObservation:
        """Wait for the boot phase to end, then report what was actually observed.

        The only settlement signal tmux offers is that the pane stopped printing. That
        is a real observation of the transient phase ending — an agent REPL emits its
        banner and then waits — and it is emphatically not an observation that the
        agent is idle rather than blocked. Hence `activity="unknown"` below: the wait
        is honest about what it waited for, and the report is honest about what it
        did not learn."""
        deadline = time.monotonic() + timeout_s
        previous, quiet_since = None, None
        while time.monotonic() < deadline:
            if handle.token not in self._windows():
                return self._observation(handle, present=False)
            screen = self.read_recent_output(handle)
            if screen != previous:
                previous, quiet_since = screen, time.monotonic()
            elif quiet_since is not None and time.monotonic() - quiet_since >= _QUIET_S:
                return self._observation(handle, present=True)
            time.sleep(_POLL_S)
        raise RuntimeBackendError(NAME, "launch_timeout", "output never quiesced")

    def wake(self, handle: RuntimeHandle, cue: str) -> None:
        """Transmit the cue and its submit keystroke in ONE tmux invocation.

        `-l` is required — without it tmux reads the text as key NAMES, so a cue
        containing anything tmux calls a key arrives as something else — and Enter
        must stay outside the literal send for the same reason, since it IS a key.
        That makes two tmux commands, and sending them as two invocations was a real
        defect rather than an inefficiency:

        `-l` puts the cue on the terminal immediately, by ordinary echo. Measured.
        The launcher's wake-echo gate looks for the cue in the runtime's own output,
        so between the two calls the gate is already satisfied by an UNSUBMITTED
        line. If the second call fails, the launcher records a woken runtime while
        the agent sits on an input line it never received — and for tmux the echo
        gate is the ONLY wedge detector, so the one detector could be fooled
        (#226 review round 1, finding 2).

        One invocation makes "cue echoed, Enter never sent" unreachable: tmux runs
        the command list or reports failure. What remains, and is inherent to reading
        a screen, is that the echo proves the cue was TRANSMITTED, not that the agent
        consumed it. That residual is the honest limit of this backend's evidence,
        not something the two-call form was buying."""
        target = self._target(handle.token)
        if self._run("send-keys", "-t", target, "-l", cue,
                     ";", "send-keys", "-t", target, "Enter").returncode:
            raise RuntimeBackendError(NAME, "backend_refused", "cue not transmitted")

    def inspect(self, handle: RuntimeHandle) -> RuntimeObservation:
        return self._observation(handle, present=handle.token in self._windows())

    def read_recent_output(self, handle: RuntimeHandle) -> str:
        proc = self._run("capture-pane", "-p", "-t", self._target(handle.token))
        if proc.returncode != 0:
            raise RuntimeBackendError(NAME, "inspect_failed", "pane not readable")
        return (proc.stdout or b"").decode(errors="replace")

    def terminate(self, handle: RuntimeHandle) -> None:
        """Idempotent, and a clean return MEANS absence (#210).

        Killing an absent window exits non-zero — measured — so a failed kill is NOT
        evidence of failure. What decides the outcome is the confirming `inspect`: the
        launcher drops the recorded handle on a clean return, and that handle is the
        only thing standing between one Work and two live runtimes. A teardown that
        cannot be confirmed must therefore RAISE, so the caller falls through to its
        own check instead of believing this one."""
        self._run("kill-window", "-t", self._target(handle.token))
        if handle.token in self._windows():
            raise RuntimeBackendError(NAME, "terminate_failed", "window still present")

    # ---- shared ---------------------------------------------------------------

    def _observation(self, handle: RuntimeHandle, *, present: bool) -> RuntimeObservation:
        """`activity` and `attention` are ALWAYS unknown. Not a placeholder to fill in
        later — tmux cannot see either, and a backend that guesses is worse than one
        that says so, because the launcher's gates would then act on invention."""
        return RuntimeObservation(backend=NAME, handle_token=handle.token,
                                  presence="present" if present else "absent",
                                  activity="unknown", attention="unknown",
                                  observed_at=_now())
