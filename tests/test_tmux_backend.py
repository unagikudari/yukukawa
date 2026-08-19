"""The tmux backend (#204 step 2), and what a second implementation is FOR.

#198 claims "stable semantics, replaceable mechanics". A contract module that names
no mechanism does not demonstrate that, and a `FakeBackend` demonstrates it least of
all: a fake is written by the same hand as the contract and agrees with it by
construction. So the load-bearing tests here drive REAL tmux.

They are opt-out rather than opt-in, unlike the herdr live suite. Starting a tmux
window in a throwaway session has no side effect outside that session and needs no
resident third-party server, so there is nothing to protect a routine `pytest` run
from — and a conformance test nobody runs proves nothing. They skip only when tmux is
genuinely absent.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from kawa.runtime.contract import (ACTIVITY, ATTENTION, PRESENCE, LaunchSpec,
                                   RuntimeBackendError, RuntimeHandle)
from kawa.runtime import tmux_backend as tb

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from tests.test_runtime_launch import _work, conn  # noqa: E402,F401  (shared fixtures)

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

_SESSION = "kawa-test-probe"


@pytest.fixture()
def session(monkeypatch):  # type: ignore[no-untyped-def]
    """A throwaway session with a stand-in REPL: prints a banner, then waits on stdin
    exactly as an agent REPL does. Using a real agent here would make the test depend
    on a model endpoint to prove a statement about tmux."""
    subprocess.run(["tmux", "kill-session", "-t", _SESSION], capture_output=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", _SESSION, "-n", "base"], check=True)
    monkeypatch.setitem(tb._COMMAND, "codex", "sh -c 'echo BANNER-READY; cat'")
    try:
        yield tb.TmuxBackend(_SESSION)
    finally:
        subprocess.run(["tmux", "kill-session", "-t", _SESSION], capture_output=True)


def test_the_contract_round_trips_against_real_tmux(session) -> None:  # type: ignore[no-untyped-def]
    """detect -> launch -> await_settled -> inspect -> wake -> terminate, on the real
    thing. Every assertion is in the CONTRACT's vocabulary — nothing tmux-shaped
    crosses the boundary, which is the property that makes the mechanics replaceable."""
    status = session.detect()
    assert status.available and status.reason == ""

    handle = session.launch(LaunchSpec("w-live", "codex", "/tmp"))
    assert handle.backend == "tmux"

    settled = session.await_settled(handle, timeout_s=15)
    assert settled.presence in PRESENCE and settled.activity in ACTIVITY
    assert settled.attention in ATTENTION
    assert settled.presence == "present"
    assert session.inspect(handle).presence == "present"

    session.wake(handle, "KAWA-CUE-XYZ")
    deadline = time.monotonic() + 5
    while "KAWA-CUE-XYZ" not in session.read_recent_output(handle):
        assert time.monotonic() < deadline, "the cue never reached the runtime's output"
        time.sleep(0.1)

    session.terminate(handle)
    assert session.inspect(handle).presence == "absent"
    session.terminate(handle)                    # idempotent: absent is success


def test_tmux_reports_unknown_rather_than_inventing_a_state(session) -> None:  # type: ignore[no-untyped-def]
    """The honest-unknown requirement, and the reason this backend was worth writing.

    tmux cannot tell WORKING from BLOCKED AT A PROMPT: both are a process holding a
    PTY with nothing new on screen. The contract says a backend that cannot
    distinguish them must say so. A backend that guessed `quiescent` here would be
    handing the launcher's gates an invention to act on."""
    handle = session.launch(LaunchSpec("w-unknown", "codex", "/tmp"))
    for observation in (session.await_settled(handle, timeout_s=15),
                        session.inspect(handle)):
        assert observation.activity == "unknown"
        assert observation.attention == "unknown"
    session.terminate(handle)


def test_a_second_launch_for_the_same_work_is_refused(session) -> None:  # type: ignore[no-untyped-def]
    """The duplicate the whole #210 apparatus exists to prevent, refused at the
    backend as well as at the launcher — two independent guards, not one."""
    handle = session.launch(LaunchSpec("w-dup", "codex", "/tmp"))
    with pytest.raises(RuntimeBackendError) as exc:
        session.launch(LaunchSpec("w-dup", "codex", "/tmp"))
    assert exc.value.error_class == "already_running"
    session.terminate(handle)


def test_a_neighbouring_window_is_never_addressed_by_mistake(session) -> None:  # type: ignore[no-untyped-def]
    """Measured, and worse than "resolves to the wrong window".

    tmux prefix-matches window targets, but only when no exact match exists — so a
    decoy alongside a LIVE runtime proves nothing, and an earlier cut of this test
    did exactly that and passed with the `=` prefix deleted.

    The hazard is the opposite arrangement: our runtime is GONE and a longer-named
    neighbour remains. Measured on tmux 3.6, `kill-window -t "sess:kawa-abc123456"`
    then kills `kawa-abc123456LONG` and exits 0. The contract REQUIRES `terminate` on
    an already-absent runtime to be a success, and the launcher calls it routinely —
    so on a node whose session holds the fleet's real agent panes, a routine idempotent
    teardown would silently kill somebody else's agent.

    Every target carries the documented `=` exact-match prefix for that reason."""
    handle = session.launch(LaunchSpec("w-target", "codex", "/tmp"))
    session.terminate(handle)
    assert session.inspect(handle).presence == "absent"

    decoy = handle.token + "LONG"                 # the arrangement that actually bites
    subprocess.run(["tmux", "new-window", "-d", "-t", _SESSION, "-n", decoy,
                    "sh", "-c", "echo DECOY; cat"], check=True)
    time.sleep(0.4)

    session.terminate(handle)                     # idempotent: absent is success
    names = subprocess.run(["tmux", "list-windows", "-t", _SESSION, "-F", "#{window_name}"],
                           capture_output=True, text=True).stdout.split()
    assert decoy in names, "an idempotent teardown killed a neighbouring window"

    # and reads cannot stray onto the neighbour either
    with pytest.raises(RuntimeBackendError) as exc:
        session.read_recent_output(handle)
    assert exc.value.error_class == "inspect_failed"


def test_terminate_raises_when_absence_cannot_be_confirmed(session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A clean return MEANS absence (#210). The launcher drops the recorded handle on
    a clean return, and that handle is the only thing standing between one Work and
    two live runtimes — so a teardown that could not be confirmed must RAISE rather
    than return and be believed."""
    handle = session.launch(LaunchSpec("w-stubborn", "codex", "/tmp"))
    monkeypatch.setattr(session, "_run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b""))
    monkeypatch.setattr(session, "_windows", lambda: [handle.token])

    with pytest.raises(RuntimeBackendError) as exc:
        session.terminate(handle)
    assert exc.value.error_class == "terminate_failed"


def test_an_unreadable_session_is_inspect_failed_not_absent(session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Collapsing "could not tell" into "absent" lets infrastructure failure be
    misread as an agent that finished — and the launcher treats absence as licence to
    start another one."""
    monkeypatch.setattr(session, "_run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"no server"))
    with pytest.raises(RuntimeBackendError) as exc:
        session.inspect(RuntimeHandle("tmux", "kawa-whatever"))
    assert exc.value.error_class == "inspect_failed"


def test_the_session_is_never_created_by_the_backend() -> None:
    """#204 REAL-6 keeps runtime-host lifecycle with the operator. A backend that
    conjured its own session would make "attached to the fleet's tmux" and "invented
    a private one" indistinguishable — and the second silently does nothing useful."""
    backend = tb.TmuxBackend("kawa-session-that-does-not-exist")
    status = backend.detect()
    assert not status.available and status.reason == "server_absent"
    names = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                           capture_output=True, text=True).stdout.split()
    assert "kawa-session-that-does-not-exist" not in names


# ---- #204 guarantee 4: the LAUNCHER's behaviours, driven by a real second backend --

def test_the_launcher_gates_hold_against_a_real_second_backend(session, conn, capsys) -> None:  # type: ignore[no-untyped-def]
    """"Backend-neutral" is a property two implementations exercise, not a claim one
    fake makes (#204 REAL-4).

    The existing suite proves the launcher never touches herdr vocabulary by running
    it against `FakeBackend`. A fake cannot falsify that: it was written to fit. This
    runs the same launcher gates against tmux, which was not."""
    import runtime_launch as rl

    _work(conn)
    handle = rl.launch(session, conn, "w-x", agent_kind="codex", cwd="/tmp",
                       loader=lambda *_: session)
    assert handle.backend == "tmux"

    # the duplicate guard, with a real recorded handle and a real liveness probe
    with pytest.raises(rl.Refused, match="already running"):
        rl.launch(session, conn, "w-x", agent_kind="codex", cwd="/tmp",
                  loader=lambda *_: session)

    assert rl.terminate("w-x", _SESSION, loader=lambda *_: session) == "terminated"
    assert rl.terminate("w-x", _SESSION, loader=lambda *_: session) == "no_runtime"


def test_the_blocked_at_launch_gate_cannot_fire_for_tmux(session, conn) -> None:  # type: ignore[no-untyped-def]
    """The honest-unknown consequence, asserted rather than left in a docstring.

    `blocked_at_launch` trips on `activity == "blocked"`, which this backend will
    never report — so for a tmux runtime the launcher has ONE wedge detector, not two:
    the cue must appear in the runtime's own output or the launch is
    `wake_echo_missing`. #204 says finding a gate shaped like herdr's observability
    would be a success of the plan. The finding is milder and worth stating plainly:
    both gates hold, but they do not hold EQUALLY, and an operator should not assume
    two where there is one."""
    import runtime_launch as rl

    _work(conn)
    handle = rl.launch(session, conn, "w-x", agent_kind="codex", cwd="/tmp",
                       loader=lambda *_: session)
    settled = session.await_settled(handle, timeout_s=15)
    assert settled.activity == "unknown"
    assert settled.activity != "blocked", "the gate's trigger is unreachable here"

    # ...and the detector that DOES work on evidence tmux can produce
    session.wake(handle, "PROOF-OF-ECHO")
    deadline = time.monotonic() + 5
    while "PROOF-OF-ECHO" not in session.read_recent_output(handle):
        assert time.monotonic() < deadline
        time.sleep(0.1)
    rl.terminate("w-x", _SESSION, loader=lambda *_: session)


def test_a_neighbouring_session_is_never_addressed_by_mistake() -> None:
    """The same prefix hazard one level up, which the first cut of this backend had.

    `=` was applied to the WINDOW component and not the SESSION component. Measured:
    `list-windows -t kawa` returns the windows of a session named `kawa-fleet` when no
    session named `kawa` exists. A backend configured for an absent session would then
    list, launch into, and kill windows in a neighbouring session that does exist — on
    this fleet, the one holding the real agent panes.

    `detect()` did not catch it. It already used `=` and correctly said
    `server_absent`, so the hazard lived entirely in the paths that run afterwards
    (#226 review round 1, finding 1)."""
    neighbour = _SESSION + "-neighbour"
    subprocess.run(["tmux", "kill-session", "-t", neighbour], capture_output=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", neighbour, "-n", "victim"], check=True)
    try:
        backend = tb.TmuxBackend(_SESSION)          # this session does NOT exist
        assert backend.detect().reason == "server_absent"
        with pytest.raises(RuntimeBackendError) as exc:
            backend.inspect(RuntimeHandle("tmux", "kawa-whatever"))
        assert exc.value.error_class == "inspect_failed"

        with pytest.raises(RuntimeBackendError):
            backend.launch(LaunchSpec("w-stray", "codex", "/tmp"))
        names = subprocess.run(["tmux", "list-windows", "-t", f"={neighbour}",
                                "-F", "#{window_name}"],
                               capture_output=True, text=True).stdout.split()
        assert names == ["victim"], f"the neighbour's windows were touched: {names}"
    finally:
        subprocess.run(["tmux", "kill-session", "-t", neighbour], capture_output=True)


def test_the_cue_and_its_submit_are_not_separable(session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`-l` echoes the cue onto the terminal immediately — measured — and the
    launcher's wake-echo gate looks for exactly that. Sent as two invocations, the
    gate is satisfied by an UNSUBMITTED line the moment the first one lands: if the
    second fails, the launcher records a woken runtime while the agent sits on input
    it never received. For tmux the echo gate is the ONLY wedge detector, so the one
    detector could be fooled (#226 review round 1, finding 2).

    Asserted structurally, because the defect IS the window between two calls and a
    behavioural test cannot see a window that only exists when the second call fails."""
    handle = session.launch(LaunchSpec("w-atomic", "codex", "/tmp"))
    calls: list[tuple] = []
    real = session._run
    monkeypatch.setattr(session, "_run",
                        lambda *a, **k: (calls.append(a), real(*a, **k))[1])

    session.wake(handle, "ATOMIC-CUE")
    sends = [a for a in calls if a and a[0] == "send-keys"]
    assert len(sends) == 1, f"the cue and its submit must not be separable: {sends}"
    assert "Enter" in sends[0] and "-l" in sends[0]
    session.terminate(handle)


def test_the_wake_is_consumed_not_merely_echoed(session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The behavioural half: a runtime that reads lines must actually RECEIVE one.

    The stand-in echoes what it consumed, so `GOT:<cue>` on screen distinguishes a
    submitted line from a cue that is merely sitting on the terminal — which is the
    difference the launcher's gate cannot see for itself."""
    monkeypatch.setitem(tb._COMMAND, "codex",
                        "sh -c 'echo BANNER-READY; while read L; do echo GOT:$L; done'")
    handle = session.launch(LaunchSpec("w-consumed", "codex", "/tmp"))
    session.await_settled(handle, timeout_s=15)
    session.wake(handle, "CONSUMED-CUE")

    deadline = time.monotonic() + 5
    while "GOT:CONSUMED-CUE" not in session.read_recent_output(handle):
        assert time.monotonic() < deadline, "the cue was echoed but never submitted"
        time.sleep(0.1)
    session.terminate(handle)


def test_every_call_site_carries_the_session_exact_match_not_just_one(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Per call site, because the first version of this test could not tell them apart.

    `_windows()` runs first in most paths, so with only IT fixed the others looked
    covered: mutating `_target` or `launch`'s `new-window` left every test passing
    while `kill-window` and `new-window` would still land in a neighbouring session.
    The masking is the point — a guard that runs early hides the absence of the guards
    behind it (#226 review round 1, mutation follow-up).

    The neighbour holds a window with EXACTLY our token name, which is the arrangement
    where a prefix-matched session target does maximum damage."""
    neighbour = _SESSION + "-neighbour"
    token = tb.token_for("w-stray")
    subprocess.run(["tmux", "kill-session", "-t", neighbour], capture_output=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", neighbour, "-n", "victim"], check=True)
    subprocess.run(["tmux", "new-window", "-d", "-t", f"={neighbour}", "-n", token,
                    "sh", "-c", "echo NEIGHBOUR; cat"], check=True)
    time.sleep(0.3)

    def _names() -> list[str]:
        return subprocess.run(["tmux", "list-windows", "-t", f"={neighbour}",
                               "-F", "#{window_name}"],
                              capture_output=True, text=True).stdout.split()

    try:
        backend = tb.TmuxBackend(_SESSION)          # this session does NOT exist
        handle = RuntimeHandle("tmux", token)

        # _target, via kill-window: the operation with teeth
        monkeypatch.setattr(backend, "_windows", lambda: [])   # unmask: skip the early guard
        backend.terminate(handle)
        assert token in _names(), "terminate reached into a neighbouring session"

        # _target, via capture-pane
        with pytest.raises(RuntimeBackendError):
            backend.read_recent_output(handle)

        # launch's new-window, with the same early guard unmasked
        with pytest.raises(RuntimeBackendError):
            backend.launch(LaunchSpec("w-stray2", "codex", "/tmp"))
        assert sorted(_names()) == sorted(["victim", token]), \
            f"launch created a window in a neighbouring session: {_names()}"
    finally:
        subprocess.run(["tmux", "kill-session", "-t", neighbour], capture_output=True)
