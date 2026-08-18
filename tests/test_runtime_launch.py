"""Launcher tests (#200 rev 3 §REAL-3, checkpoint 2).

The launcher is where the two guarantees that matter live: only the fixed
cue ever reaches a PTY, and one Work never gets two live runtimes. Both are
tested against behaviour, not against the implementation's own vocabulary —
the seal is checked by summing every byte a backend was asked to transmit
across a whole launch, so a future `send_keys` path would fail the test
without anyone remembering to update it.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from kawa.runtime.contract import (BackendStatus, LaunchSpec, RuntimeBackendError,
                                   RuntimeHandle, RuntimeObservation)
from kawa.runtime.wake import WAKE_CUE

psycopg = pytest.importorskip("psycopg")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

import runtime_launch as rl  # noqa: E402

_TABLES = ("content_embedding, event_content, events, event_links, event_link, "
           "event_observation, event_claim, event_plan, event_work, "
           "event_work_dependency, event_work_retired, event_result, "
           "current_claim_standing, current_plans, current_work, "
           "current_work_dependency, runtime_work_occupancy, work_dispatch, "
           "situation_rollup, fleet_node, evidence_provenance, projection_state, "
           "fleet_node_facet")


class FakeBackend:
    """A backend that records every byte it was asked to transmit."""
    name = "fake"

    def __init__(self, *, settle="quiescent", after_cue="quiescent",
                 echo=True, present_after_launch=True,
                 terminate_fails=False, inspect_fails=False):
        self.transmitted: list[str] = []
        self.launched: list[str] = []
        self.terminated: list[str] = []
        self.screen = "boot banner"
        self._settle, self._after_cue = settle, after_cue
        self._echo, self._present = echo, present_after_launch
        self._woken = False
        # #210: the happy path was the only one the fake could express, so the
        # forget-without-proof defect was untestable. A backend whose teardown
        # or liveness answer fails is the ordinary case (an unreachable server),
        # not an exotic one.
        self._terminate_fails, self._inspect_fails = terminate_fails, inspect_fails

    def detect(self):
        return BackendStatus(True, "", "0.8.0")

    def launch(self, spec: LaunchSpec) -> RuntimeHandle:
        self.launched.append(spec.work_ref)
        return RuntimeHandle(self.name, "tok-" + spec.work_ref)

    def _obs(self, handle, activity):
        return RuntimeObservation(self.name, handle.token,
                                  "present" if self._present else "absent",
                                  activity, "not_needed", "2026-08-18T00:00:00Z")

    def await_settled(self, handle, timeout_s=60.0):
        return self._obs(handle, self._after_cue if self._woken else self._settle)

    def wake(self, handle, cue):
        self.transmitted.append(cue)
        self._woken = True
        # `echo` may be an int: the number of leading attempts the runtime
        # swallows before one lands — the live boot race, made testable
        lands = self._echo is True or (self._echo is not False
                                       and len(self.transmitted) > int(self._echo))
        if lands:
            # a real pane re-flows the submitted line across rows
            self.screen += "\n> " + "\n  ".join(cue.split(" ", 4))

    def inspect(self, handle):
        if self._inspect_fails:
            raise RuntimeBackendError(self.name, "inspect_failed", "cannot answer")
        return self._obs(handle, self._after_cue if self._woken else self._settle)

    def read_recent_output(self, handle):
        return self.screen

    def terminate(self, handle):
        if self._terminate_fails:
            raise RuntimeBackendError(self.name, "terminate_failed", "server unreachable")
        self.terminated.append(handle.token)


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):  # type: ignore[no-untyped-def]
    """Real delivery gets a 90s budget; the tests exercise the same code with
    the clock compressed, so a retry loop is proven rather than waited out."""
    monkeypatch.setattr(rl, "_WAKE_DEADLINE_S", 0.6)
    monkeypatch.setattr(rl, "_WAKE_RETRY_S", 0.05)


@pytest.fixture()
def conn(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_TABLES}")
    c.commit()
    monkeypatch.setenv("KAWA_RUNTIME_HANDLES", str(tmp_path / "handles.json"))
    monkeypatch.setenv("HOME", str(tmp_path))
    yield c
    c.rollback()
    c.close()


def _work(conn, work_ref="w-x", execution="ready"):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("INSERT INTO current_work (work_ref, plan_ref, work_kind, execution, "
                    "awareness, eligibility, coordination, authority, latest_event_id) "
                    "VALUES (%s,'p','implement',%s,'current','eligible','not_required',"
                    "'not_required','sha256:0')", (work_ref, execution))
    conn.commit()


# ---- the seal: only the constant ever reaches a PTY ----

def test_total_bytes_reaching_the_runtime_are_exactly_the_cue(conn):  # type: ignore[no-untyped-def]
    _work(conn)
    backend = FakeBackend()
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert backend.transmitted == [WAKE_CUE]        # not "contains", not "starts with"
    assert "".join(backend.transmitted) == WAKE_CUE


def test_cue_carries_no_work_reference_or_prose():  # type: ignore[no-untyped-def]
    # the constant itself must stay content-free: no placeholders to fill,
    # no identifiers to leak, nothing an agent could mistake for orders
    assert "{" not in WAKE_CUE and "%" not in WAKE_CUE
    for word in ("w-", "plan-", "objective", "http"):
        assert word not in WAKE_CUE.lower()


def test_launcher_exposes_no_way_to_pass_a_prompt():  # type: ignore[no-untyped-def]
    import inspect
    source = inspect.getsource(rl)
    assert "WAKE_CUE" in source
    # no formatting of the cue anywhere: the seal is that the constant is
    # transmitted verbatim, so any f-string or concatenation around it is a
    # defect this assertion catches early
    for pattern in ("WAKE_CUE +", "WAKE_CUE.format", "f\"{WAKE_CUE", "WAKE_CUE %"):
        assert pattern not in source
    assert "cue" not in {p for p in inspect.signature(rl.launch).parameters}


# ---- actionability comes from the projection ----

@pytest.mark.parametrize("execution", ["blocked", "finished", "retired"])
def test_non_actionable_work_is_refused(conn, execution):  # type: ignore[no-untyped-def]
    _work(conn, execution=execution)
    backend = FakeBackend()
    with pytest.raises(rl.Refused, match="not actionable"):
        rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert backend.launched == []


def test_unknown_work_is_refused(conn):  # type: ignore[no-untyped-def]
    with pytest.raises(rl.Refused, match="no such Work"):
        rl.launch(FakeBackend(), conn, "w-nope", agent_kind="codex", cwd="/tmp")


# ---- duplicate guard ----

def test_second_launch_for_the_same_work_is_refused(conn):  # type: ignore[no-untyped-def]
    _work(conn)
    backend = FakeBackend()
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    with pytest.raises(rl.Refused, match="already running"):
        rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert backend.launched == ["w-x"]              # exactly one runtime


def test_stale_entry_does_not_block_a_relaunch(conn):  # type: ignore[no-untyped-def]
    _work(conn)
    backend = FakeBackend()
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    backend._present = False                        # the runtime died meanwhile
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert backend.launched == ["w-x", "w-x"]


def test_unknown_liveness_refuses_rather_than_guessing(conn):  # type: ignore[no-untyped-def]
    _work(conn)
    backend = FakeBackend()
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")

    def blind(handle):
        raise RuntimeBackendError("fake", "inspect_failed")
    backend.inspect = blind
    with pytest.raises(rl.Refused, match="liveness unknown"):
        rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert backend.launched == ["w-x"]               # no second runtime on a guess


def test_force_replaces_the_recorded_runtime(conn):  # type: ignore[no-untyped-def]
    _work(conn)
    backend = FakeBackend()
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp", force=True)
    assert backend.terminated == ["tok-w-x"]         # the old one was torn down first
    assert backend.launched == ["w-x", "w-x"]


# ---- wedge handling ----

def test_blocked_at_launch_tears_down_and_deregisters(conn):  # type: ignore[no-untyped-def]
    _work(conn)
    backend = FakeBackend(settle="blocked")
    with pytest.raises(RuntimeBackendError) as excinfo:
        rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert excinfo.value.error_class == "blocked_at_launch"
    assert backend.terminated == ["tok-w-x"]         # nothing left to absorb the next cue
    assert backend.transmitted == []                 # and the cue was never sent
    from kawa.runtime.handles import locked
    with locked() as cache:
        assert cache.get("w-x") is None              # no phantom entry either


def test_permanently_swallowed_cue_is_named_and_cleaned_up(conn):  # type: ignore[no-untyped-def]
    # the boot race that never resolves: retried, then reported honestly
    _work(conn)
    backend = FakeBackend(echo=False)
    with pytest.raises(RuntimeBackendError) as excinfo:
        rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert excinfo.value.error_class == "wake_echo_missing"
    assert len(backend.transmitted) > 1                  # it did retry
    assert set(backend.transmitted) == {WAKE_CUE}        # and only ever the constant
    assert backend.terminated == ["tok-w-x"]


def test_cue_swallowed_during_boot_lands_on_a_retry(conn):  # type: ignore[no-untyped-def]
    # measured live 2026-08-18: the runtime reports interactive_ready and
    # settles quiescent while its REPL is still coming up, accepts the
    # submission and drops it. The second attempt lands.
    _work(conn)
    backend = FakeBackend(echo=1)
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert backend.transmitted == [WAKE_CUE, WAKE_CUE]   # retried, never rephrased
    assert backend.terminated == []                      # and the runtime survives


def test_blocked_after_the_cue_is_reported(conn):  # type: ignore[no-untyped-def]
    _work(conn)
    backend = FakeBackend(after_cue="blocked")
    with pytest.raises(RuntimeBackendError) as excinfo:
        rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert excinfo.value.error_class == "blocked_at_launch"


def test_echo_rendered_late_is_not_missed_by_the_next_attempt(conn):  # type: ignore[no-untyped-def]
    """Review finding 1: attempt N's echo can render only after attempt N's
    check. Comparing each attempt against the PREVIOUS screen would then
    demand further change from a runtime that had already answered; the
    baseline is taken once, before anything is sent."""
    _work(conn)
    backend = FakeBackend(echo=False)               # never echoes on its own

    real_wake = backend.wake
    def late_wake(handle, cue):                     # noqa: E306
        real_wake(handle, cue)
        if len(backend.transmitted) == 1:           # the first cue lands, late
            backend.screen += "\n> " + "\n  ".join(cue.split(" ", 4))
    backend.wake = late_wake

    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert len(backend.transmitted) <= 2            # noticed, not retried to death
    assert backend.terminated == []


def test_colourised_echo_still_counts_as_delivered(conn):  # type: ignore[no-untyped-def]
    """Review finding 2: a runtime that colourises its echo must not look
    like a runtime that never received the cue."""
    _work(conn)
    backend = FakeBackend()
    real_wake = backend.wake
    def coloured(handle, cue):                      # noqa: E306
        backend.transmitted.append(cue)
        backend._woken = True
        backend.screen += "\n\x1b[1;32m> " + "\x1b[0m\n  ".join(cue.split(" ", 4)) + "\x1b[0m"
    backend.wake = coloured
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert backend.transmitted == [WAKE_CUE]
    assert "\x1b[" in backend.screen                # the escapes really were there


def test_status_file_write_is_atomic(conn, tmp_path):  # type: ignore[no-untyped-def]
    """Review finding 3: a dashboard polling this file must never catch it
    half-written, so it lands by atomic replace like the handle cache."""
    _work(conn)
    rl.launch(FakeBackend(), conn, "w-x", agent_kind="codex", cwd="/tmp")
    status = tmp_path / ".kawa" / "status" / "runtime.status"
    json.load(open(status, encoding="utf-8"))        # parses = not torn
    assert oct(status.stat().st_mode)[-3:] == "600"
    leftovers = [p for p in status.parent.iterdir() if p.name != "runtime.status"]
    assert leftovers == []                          # no temp files left behind


def test_wrapped_echo_still_counts_as_delivered(conn):  # type: ignore[no-untyped-def]
    # the fake re-flows the cue across rows exactly as a narrow pane does
    _work(conn)
    backend = FakeBackend()
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert "\n" in backend.screen and WAKE_CUE not in backend.screen  # never verbatim
    assert backend.transmitted == [WAKE_CUE]


# ---- telemetry must not become truth ----

def test_status_file_is_display_only_and_writes_nothing_to_kawa(conn, tmp_path):  # type: ignore[no-untyped-def]
    _work(conn)
    backend = FakeBackend()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events")
        before = cur.fetchone()[0]
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events")
        assert cur.fetchone()[0] == before          # the launcher records NOTHING
        cur.execute("SELECT count(*) FROM runtime_work_occupancy")
        assert cur.fetchone()[0] == 0               # and claims nothing
        cur.execute("SELECT execution FROM current_work WHERE work_ref='w-x'")
        assert cur.fetchone()[0] == "ready"         # Work state untouched

    status = json.load(open(tmp_path / ".kawa" / "status" / "runtime.status", encoding="utf-8"))
    assert "never an input to Work state" in status["_contract"]
    assert set(status["runtimes"]["w-x"]) == {"backend", "handle_token", "presence",
                                              "activity", "attention", "observed_at"}


def test_terminate_removes_the_entry(conn):  # type: ignore[no-untyped-def]
    _work(conn)
    backend = FakeBackend()
    rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert rl.terminate(backend, "w-x") is True
    assert rl.terminate(backend, "w-x") is False    # idempotent
    assert backend.terminated == ["tok-w-x"]


# --- #210: forgetting a runtime requires PROOF of absence ---------------------
# The guard that keeps one Work from getting two live runtimes is the recorded
# handle. Deleting it after a termination that never confirmed converts a
# recoverable cleanup failure into an undetectable duplicate: the runtime is
# still alive and nothing points at it any more.

def test_failed_teardown_retains_the_handle_and_says_so(conn, capsys):  # type: ignore[no-untyped-def]
    _work(conn)
    backend = FakeBackend(settle="blocked", terminate_fails=True)
    with pytest.raises(RuntimeBackendError) as excinfo:
        rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")

    assert excinfo.value.error_class == "blocked_at_launch"      # original failure leads
    assert "cleanup_incomplete" in capsys.readouterr().err       # and is not the only news

    from kawa.runtime.handles import locked
    with locked() as cache:
        assert cache.get("w-x") is not None      # the only locator survives


def test_a_retained_handle_still_refuses_the_next_launch(conn):  # type: ignore[no-untyped-def]
    """The point of retaining it: the duplicate is still refused afterwards."""
    _work(conn)
    with pytest.raises(RuntimeBackendError):
        rl.launch(FakeBackend(settle="blocked", terminate_fails=True),
                  conn, "w-x", agent_kind="codex", cwd="/tmp")

    fresh = FakeBackend()                        # the runtime is in fact still there
    with pytest.raises(rl.Refused) as excinfo:
        rl.launch(fresh, conn, "w-x", agent_kind="codex", cwd="/tmp")
    assert "already running" in str(excinfo.value)
    assert fresh.launched == []                  # no second runtime


def test_unanswerable_liveness_after_failed_teardown_also_retains(conn):  # type: ignore[no-untyped-def]
    """An inspect that cannot answer is not absence either."""
    _work(conn)
    backend = FakeBackend(settle="blocked", terminate_fails=True, inspect_fails=True)
    with pytest.raises(RuntimeBackendError):
        rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    from kawa.runtime.handles import locked
    with locked() as cache:
        assert cache.get("w-x") is not None


def test_failed_teardown_confirmed_absent_does_drop(conn):  # type: ignore[no-untyped-def]
    """Proof by inspect is proof: terminate raised, but the runtime is gone."""
    _work(conn)
    backend = FakeBackend(settle="blocked", terminate_fails=True,
                          present_after_launch=False)
    with pytest.raises(RuntimeBackendError):
        rl.launch(backend, conn, "w-x", agent_kind="codex", cwd="/tmp")
    from kawa.runtime.handles import locked
    with locked() as cache:
        assert cache.get("w-x") is None          # absence PROVEN, so forgetting is allowed


def test_force_will_not_replace_an_unproven_runtime(conn, capsys):  # type: ignore[no-untyped-def]
    """--force overrides the operator's uncertainty, not the need for proof."""
    _work(conn)
    rl.launch(FakeBackend(), conn, "w-x", agent_kind="codex", cwd="/tmp")

    stubborn = FakeBackend(terminate_fails=True)
    with pytest.raises(rl.Refused) as excinfo:
        rl.launch(stubborn, conn, "w-x", agent_kind="codex", cwd="/tmp", force=True)

    assert "cleanup_incomplete" in str(excinfo.value)
    assert stubborn.launched == []                       # NO replacement runtime
    assert "cleanup_incomplete" in capsys.readouterr().err
    from kawa.runtime.handles import locked
    with locked() as cache:
        assert cache.get("w-x") is not None              # old handle still recorded


def test_operator_terminate_keeps_the_entry_when_unproven(conn, capsys):  # type: ignore[no-untyped-def]
    """The same rule on the operator path — an unreachable backend must not
    silently lose the locator."""
    _work(conn)
    rl.launch(FakeBackend(), conn, "w-x", agent_kind="codex", cwd="/tmp")

    assert rl.terminate(FakeBackend(terminate_fails=True), "w-x") is False
    assert "cleanup_incomplete" in capsys.readouterr().err
    from kawa.runtime.handles import locked
    with locked() as cache:
        assert cache.get("w-x") is not None

    assert rl.terminate(FakeBackend(), "w-x") is True    # and it is still terminable
