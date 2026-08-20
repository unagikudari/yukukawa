"""Live conformance for the herdr backend (#200 rev 3 §REAL-6).

The fake-replay suite proves the adapter parses what Herdr SAID on
2026-08-18. It cannot prove Herdr still says it. This module closes that
gap two ways:

  * `conformance.json` records exactly which runtime the captures came from
    (version, protocol, schema digest). A test binds the code's version pin
    to that record, so raising `MIN_VERSION` without re-capturing fails.
  * a live smoke drives a REAL runtime through detect → launch → inspect →
    terminate. It needs `KAWA_HERDR_LIVE=1` in addition to a present binary
    and server: unlike every other test here it has a side effect (it starts
    an actual agent process), so it must be asked for, never inherited by a
    routine `pytest` run on a node that happens to have Herdr resident.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

from kawa.runtime.contract import LaunchSpec, RuntimeBackendError
from kawa.runtime.herdr_backend import MIN_VERSION, HerdrBackend, token_for

_CONFORMANCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "herdr", "conformance.json")


def _record() -> dict:
    with open(_CONFORMANCE, encoding="utf-8") as fh:
        return json.load(fh)


def test_version_pin_matches_the_captured_runtime():  # type: ignore[no-untyped-def]
    record = _record()
    captured = tuple(int(p) for p in record["version"].split("."))
    assert MIN_VERSION <= captured, "pin exceeds the version the captures came from"
    assert record["schema_sha256"].startswith("sha256:")
    assert record["protocol"] >= 19


@pytest.mark.skipif(os.environ.get("KAWA_HERDR_LIVE") != "1",
                    reason="live conformance is opt-in: it starts a real agent")
def test_live_runtime_round_trip():  # type: ignore[no-untyped-def]
    session = os.environ.get("KAWA_HERDR_SESSION", "kawa-conformance")
    binary = shutil.which("herdr")
    if not binary:
        pytest.skip("herdr binary absent")
    backend = HerdrBackend(session, binary=binary)
    status = backend.detect()
    if not status.available:
        pytest.skip(f"herdr unavailable: {status.reason}")
    assert status.version == _record()["version"], "live runtime drifted from captures"

    work_ref = "w-conformance-smoke"
    handle = backend.launch(LaunchSpec(work_ref, "codex", os.path.expanduser("~")))
    try:
        assert handle.token == token_for(work_ref)
        settled = backend.await_settled(handle, timeout_s=60)
        assert settled.presence == "present"
        assert settled.activity in ("quiescent", "blocked")   # real states, either is honest
        assert backend.read_recent_output(handle) != ""
        # a second launch for the same Work must not mint a second runtime —
        # the launcher's flock guard (checkpoint 2) is the primary defence,
        # but the backend refuses on its own, and only live can prove it
        with pytest.raises(RuntimeBackendError) as second:
            backend.launch(LaunchSpec(work_ref, "codex", os.path.expanduser("~")))
        assert second.value.error_class == "already_running"
    finally:
        backend.terminate(handle)
    assert backend.inspect(handle).presence == "absent"       # cleanup verified
    with pytest.raises(RuntimeBackendError):
        HerdrBackend(session, binary="/nonexistent/herdr")._run("workspace", "list")


@pytest.mark.skipif(os.environ.get("KAWA_HERDR_LIVE") != "1",
                    reason="live conformance is opt-in: it starts a real agent")
def test_live_wake_delivery_is_proven_by_the_runtimes_own_output(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """The property fixtures cannot show: a real REPL accepts the cue while
    still booting and silently drops it, so delivery is only real when the
    runtime's own screen says so. This drives the launcher's wake path
    against a live runtime — no database and no Work needed."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts"))
    import runtime_launch as rl

    from kawa.runtime.wake import WAKE_CUE
    session = os.environ.get("KAWA_HERDR_SESSION", "kawa-conformance")
    binary = shutil.which("herdr")
    if not binary:
        pytest.skip("herdr binary absent")
    backend = HerdrBackend(session, binary=binary)
    if not backend.detect().available:
        pytest.skip("herdr server absent")
    # HOME is deliberately NOT redirected here: the runtime locates its own
    # socket under the real home, so a sandboxed HOME would make a healthy
    # server look absent. Only the launcher's own state file is redirected —
    # and this path writes none of it.
    monkeypatch.setenv("KAWA_RUNTIME_HANDLES", str(tmp_path / "handles.json"))

    handle = backend.launch(LaunchSpec("w-live-wake", "codex", os.path.expanduser("~")))
    try:
        backend.await_settled(handle, timeout_s=90)
        rl.deliver_wake(backend, handle)                     # raises if never echoed
        screen = " ".join(backend.read_recent_output(handle).split())
        assert " ".join(WAKE_CUE.split(";")[0].split()) in screen
    finally:
        backend.terminate(handle)


def test_the_wake_path_does_not_consult_the_prompt_primitive() -> None:
    """#204 step 3's decision, pinned to what the code EXECUTES.

    Two earlier attempts at this pin were both wrong, and the way they were wrong is
    the useful part:

      * The first drove a real agent and FAILED when the primitive agreed with the
        gate, as an alarm for the recorded measurement expiring. A claude turn that
        settles inside the timeout returns `ok` as ordinary latency variance — a test
        that fails on someone else's latency gets deleted by whoever hits it, taking
        the pin with it.
      * The second asserted `"--wait" not in inspect.getsource(wake)`. That tests
        SPELLING. It passes the moment the flag moves into a helper, a module
        constant, a kwargs dict, or a joined string — none of which change what runs.

    So this asserts the argument vector that actually reaches the runtime. A swap
    made by any of those routes still has to put the flag on the command line, and
    that is what is checked."""
    from kawa.runtime.contract import RuntimeHandle
    from kawa.runtime.herdr_backend import HerdrBackend
    from kawa.runtime.wake import WAKE_CUE

    dispatched: list[tuple] = []

    class _Recording(HerdrBackend):
        def _run_json(self, *args, **kw):          # type: ignore[no-untyped-def]
            dispatched.append(args)
            return {}

        def _run(self, *args, **kw):               # type: ignore[no-untyped-def]
            dispatched.append(args)
            raise AssertionError(f"wake reached the raw runner: {args}")

    backend = _Recording("kawa", binary="/nonexistent/herdr")
    backend.wake(RuntimeHandle("herdr", "kawa-testtoken"), WAKE_CUE)

    assert dispatched, "wake dispatched nothing at all"
    flat = [str(a) for call in dispatched for a in call]
    assert "--wait" not in flat, (
        f"the wake path now passes --wait: {dispatched}. #204 step 3 measured that "
        "the primitive reports SETTLEMENT rather than DELIVERY and declined the swap; "
        "re-argue it there rather than changing this quietly.")
    assert any("prompt" in str(call) or "submit" in str(call) for call in dispatched), (
        f"wake no longer submits anything — this pin has drifted off its subject: "
        f"{dispatched}")
