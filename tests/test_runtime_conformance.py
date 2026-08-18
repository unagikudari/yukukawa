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
