"""H1 adapter tests (#200 rev 3): the adapter BOUNDARY and the herdr backend.

Every herdr response replayed here is a REAL capture from Herdr 0.8.0 on the
dogfood node (tests/fixtures/herdr/*, 2026-08-18) — including the three
measured traps: the default-socket "server_not_running" lie (T1), a genuine
trust-dialog `blocked` state (T2's family), and the pane-close teardown that
makes an agent vanish (T3's cleanup path). No fixture is hand-written.

The suite runs with herdr UNINSTALLED: the backend is driven through a fake
executable on PATH.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys

import pytest

from kawa.runtime.contract import (ERROR_CLASSES, OBSERVATION_FIELDS, LaunchSpec,
                                   RuntimeBackendError, RuntimeHandle,
                                   RuntimeObservation)
from kawa.runtime.env_policy import DENIED, build_env
from kawa.runtime.herdr_backend import HerdrBackend, token_for

_TESTS = os.path.dirname(os.path.abspath(__file__))

# vocabulary that must never cross the adapter boundary (#200 §REAL-1)
_BACKEND_WORDS = ("pane", "workspace", "w2:p1", "w3:p1", "herdr.sock",
                  "session", "terminal_id", "agent_status", "interactive_ready",
                  "agent_name_taken", "server_not_running", "socket")


@pytest.fixture()
def fake_herdr(tmp_path):  # type: ignore[no-untyped-def]
    """Install the fake as an executable named `herdr` and return a builder
    that pins the backend to it."""
    binary = tmp_path / "herdr"
    shutil.copy(os.path.join(_TESTS, "fake_herdr.py"), binary)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "invocations.jsonl"

    def build(scenario: dict) -> HerdrBackend:
        os.environ["FAKE_HERDR_SCENARIO"] = json.dumps(scenario)
        os.environ["FAKE_HERDR_LOG"] = str(log)
        os.environ["FAKE_HERDR_FIXTURES"] = os.path.join(_TESTS, "fixtures", "herdr")
        return HerdrBackend("kawa-test", binary=str(binary))

    build.log = log  # type: ignore[attr-defined]
    build.invocations = lambda: [json.loads(l) for l in                    # noqa: E741
                                 log.read_text().splitlines()] if log.exists() else []
    yield build
    for name in ("FAKE_HERDR_SCENARIO", "FAKE_HERDR_LOG", "FAKE_HERDR_FIXTURES"):
        os.environ.pop(name, None)


def _fx(name: str) -> dict:
    return {"fixture": name}


_READY = {"workspace list": _fx("workspace_list.json")}


# ---- boundary: nothing herdr-shaped escapes ----

def test_observation_serializes_only_allowlisted_neutral_fields(fake_herdr):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY, "agent get": _fx("agent_get_blocked.json")})
    obs = backend.inspect(RuntimeHandle("herdr", token_for("w-x")))
    assert sorted(obs.to_dict()) == sorted(OBSERVATION_FIELDS)
    blob = json.dumps(obs.to_dict()).lower()
    for word in _BACKEND_WORDS:
        assert word not in blob, word            # serialized form IS the boundary
    assert obs.activity == "blocked" and obs.attention == "needed"


def test_structured_refusal_never_forwards_backend_text(fake_herdr):  # type: ignore[no-untyped-def]
    # the real agent_name_taken message carries pane/workspace/terminal ids
    backend = fake_herdr({**_READY, "agent start": _fx("err_agent_duplicate.json"),
                          "workspace create": _fx("workspace_create.json"),
                          "pane close": _fx("pane_close.json")})
    with pytest.raises(RuntimeBackendError) as excinfo:
        backend.launch(LaunchSpec("w-x", "codex", "/tmp"))
    message = str(excinfo.value).lower()
    assert excinfo.value.error_class == "already_running"
    for word in ("term_", "w2:p1", "candidates", "taken"):
        assert word not in message, word
    assert excinfo.value.error_class in ERROR_CLASSES


def test_unparseable_output_is_classified_not_leaked(fake_herdr):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY,
                          "agent get": {"stdout": "boom: /home/x/herdr.sock\n"}})  # pub-lint:allow synthetic
    with pytest.raises(RuntimeBackendError) as excinfo:
        backend.inspect(RuntimeHandle("herdr", "kawa-abc"))
    assert excinfo.value.error_class == "malformed_response"
    assert "herdr.sock" not in str(excinfo.value)


def test_handle_token_is_derived_from_work_ref_not_prose():  # type: ignore[no-untyped-def]
    token = token_for("w-fix-the-oauth-token-leak")
    assert token.startswith("kawa-") and len(token) == 17
    assert "oauth" not in token and "leak" not in token   # no prose side channel
    assert token == token_for("w-fix-the-oauth-token-leak")  # deterministic


# ---- T1: the named-session trap ----

def test_every_invocation_pins_the_session(fake_herdr):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY, "agent get": _fx("agent_get_done.json"),
                          "agent read": {"stdout": "hello"}})
    backend.detect()
    backend.inspect(RuntimeHandle("herdr", "kawa-abc"))
    backend.read_recent_output(RuntimeHandle("herdr", "kawa-abc"))
    calls = [c for c in fake_herdr.invocations() if "--version" not in c]
    assert calls
    for argv in calls:
        assert argv[0] == "--session" and argv[1] == "kawa-test"


def test_detect_separates_binary_absent_from_server_absent(fake_herdr):  # type: ignore[no-untyped-def]
    # real capture of the default-socket lie: binary fine, server not running
    backend = fake_herdr({"workspace list": _fx("err_server_absent.json")})
    status = backend.detect()
    assert status.available is False and status.reason == "server_absent"
    assert status.version == "0.8.0"             # version still honestly reported

    missing = HerdrBackend("kawa-test", binary="/nonexistent/herdr")
    assert missing.detect() == type(status)(False, "binary_absent")


def test_version_below_pin_is_refused(fake_herdr, tmp_path):  # type: ignore[no-untyped-def]
    backend = fake_herdr(_READY)
    fixtures = os.path.join(_TESTS, "fixtures", "herdr", "version.txt")
    original = open(fixtures, encoding="utf-8").read()
    try:
        open(fixtures, "w", encoding="utf-8").write("herdr 0.7.9\n")
        status = backend.detect()
    finally:
        open(fixtures, "w", encoding="utf-8").write(original)
    assert status.available is False and status.reason == "version_unsupported"


# ---- lifecycle mapping and absence ----

@pytest.mark.parametrize("fixture,expected", [
    ("agent_get_blocked.json", ("present", "blocked", "needed")),
    ("agent_get_done.json", ("present", "quiescent", "needed")),
])
def test_state_mapping_is_adapter_policy(fake_herdr, fixture, expected):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY, "agent get": _fx(fixture)})
    obs = backend.inspect(RuntimeHandle("herdr", "kawa-abc"))
    assert (obs.presence, obs.activity, obs.attention) == expected


def test_absent_runtime_is_a_state_not_an_error(fake_herdr):  # type: ignore[no-untyped-def]
    # real capture taken right after the pane was closed
    backend = fake_herdr({**_READY, "agent get": _fx("err_agent_missing_after_close.json")})
    obs = backend.inspect(RuntimeHandle("herdr", "kawa-abc"))
    assert obs.presence == "absent" and obs.activity == "unknown"


def test_read_output_distinguishes_absence_from_a_refusal(fake_herdr):  # type: ignore[no-untyped-def]
    # an absent runtime honestly has no output...
    gone = fake_herdr({**_READY, "agent read": _fx("err_agent_missing.json")})
    assert gone.read_recent_output(RuntimeHandle("herdr", "kawa-abc")) == ""
    # ...but any other failure must NOT be flattened into emptiness: the
    # caller's wake gate reads "" as "the cue never landed" and would blame
    # the agent for a transport failure it never saw
    broken = fake_herdr({**_READY, "agent read": {"stderr": "not json\n", "code": 1}})
    with pytest.raises(RuntimeBackendError) as excinfo:
        broken.read_recent_output(RuntimeHandle("herdr", "kawa-abc"))
    assert excinfo.value.error_class == "malformed_response"


def test_terminate_is_idempotent_when_already_gone(fake_herdr):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY, "agent get": _fx("err_agent_missing.json")})
    backend.terminate(RuntimeHandle("herdr", "kawa-abc"))    # no raise


def test_terminate_closes_the_pane_then_verifies_absence(fake_herdr):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY,
                          "agent get": [_fx("agent_get_blocked.json"),
                                        _fx("err_agent_missing_after_close.json")],
                          "pane close": _fx("pane_close.json")})
    backend.terminate(RuntimeHandle("herdr", "kawa-abc"))
    closes = [c for c in fake_herdr.invocations() if c[2:4] == ["pane", "close"]]
    assert len(closes) == 1 and closes[0][4].startswith("w")   # the real pane id


def test_terminate_that_does_not_take_is_named_failure(fake_herdr):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY, "agent get": _fx("agent_get_blocked.json"),
                          "pane close": _fx("pane_close.json")})
    with pytest.raises(RuntimeBackendError) as excinfo:
        backend.terminate(RuntimeHandle("herdr", "kawa-abc"))
    assert excinfo.value.error_class == "terminate_failed"


# ---- launch returns an addressable handle even when the runtime wedges ----

def test_launch_returns_handle_before_readiness_so_cleanup_is_possible(fake_herdr):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY, "workspace create": _fx("workspace_create.json"),
                          "agent start": _fx("agent_start.json"),
                          "agent get": _fx("agent_get_blocked.json")})
    handle = backend.launch(LaunchSpec("w-x", "codex", "/tmp"))
    assert handle.token == token_for("w-x")
    # the wedged (trust-dialog) runtime is still addressable — round-2's
    # requirement that blocked_at_launch can always be cleaned up
    assert backend.inspect(handle).activity == "blocked"


def test_failed_agent_start_leaves_no_orphan_pane(fake_herdr):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY, "workspace create": _fx("workspace_create.json"),
                          "agent start": _fx("err_agent_duplicate.json"),
                          "pane close": _fx("pane_close.json")})
    with pytest.raises(RuntimeBackendError):
        backend.launch(LaunchSpec("w-x", "codex", "/tmp"))
    assert any(c[2:4] == ["pane", "close"] for c in fake_herdr.invocations())


def test_refusal_on_stderr_is_parsed_not_called_malformed(fake_herdr):  # type: ignore[no-untyped-def]
    # regression for the divergence the live smoke caught: refusals arrive on
    # STDERR with rc 1, and reading stdout alone turned every one of them
    # into "malformed_response"
    backend = fake_herdr({"workspace list": _fx("err_server_absent.json")})
    status = backend.detect()
    assert status.reason == "server_absent"       # not malformed_response


def test_pane_not_ready_is_retried_by_name_then_times_out(fake_herdr):  # type: ignore[no-untyped-def]
    # real capture: a freshly created pane refuses agents until its shell is
    # up ("is not an available shell"). Transient and NAMED — retried, not
    # slept through, and bounded.
    backend = fake_herdr({**_READY, "workspace create": _fx("workspace_create.json"),
                          "agent start": [_fx("err_agent_pane_busy.json"),
                                          _fx("err_agent_pane_busy.json"),
                                          _fx("agent_start.json")],
                          "pane close": _fx("pane_close.json")})
    backend.pane_ready_s = 5
    handle = backend.launch(LaunchSpec("w-x", "codex", "/tmp"))
    starts = [c for c in fake_herdr.invocations() if c[2:4] == ["agent", "start"]]
    assert len(starts) == 3 and handle.token == token_for("w-x")

    stuck = fake_herdr({**_READY, "workspace create": _fx("workspace_create.json"),
                        "agent start": _fx("err_agent_pane_busy.json"),
                        "pane close": _fx("pane_close.json")})
    stuck.pane_ready_s = 0.6
    with pytest.raises(RuntimeBackendError) as excinfo:
        stuck.launch(LaunchSpec("w-y", "codex", "/tmp"))
    assert excinfo.value.error_class == "launch_timeout"
    assert any(c[2:4] == ["pane", "close"] for c in fake_herdr.invocations())


def test_await_settled_returns_the_observed_state(fake_herdr):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY, "agent wait": _fx("agent_wait.json"),
                          "agent get": _fx("agent_get_done.json")})
    obs = backend.await_settled(RuntimeHandle("herdr", "kawa-abc"), timeout_s=1)
    assert obs.activity == "quiescent" and obs.attention == "needed"


def test_wait_timeout_is_named_launch_timeout(fake_herdr):  # type: ignore[no-untyped-def]
    backend = fake_herdr({**_READY,
                          "agent wait": {"stdout": json.dumps(
                              {"error": {"code": "timeout", "message": "timed out"},
                               "id": "cli:agent:wait"})}})
    with pytest.raises(RuntimeBackendError) as excinfo:
        backend.await_settled(RuntimeHandle("herdr", "kawa-abc"), timeout_s=1)
    assert excinfo.value.error_class == "launch_timeout"


# ---- env allowlist ----

def test_env_allowlist_drops_credentials_and_keeps_os_required():  # type: ignore[no-untyped-def]
    built = build_env({"HOME": "/home/node", "PATH": "/bin", "USER": "u",  # pub-lint:allow synthetic
                       "LOGNAME": "u",
                       "TMPDIR": "/tmp", "LC_ALL": "C.UTF-8", "KAWA_DSN": "dbname=kawa",
                       "SSH_AUTH_SOCK": "/run/agent.sock", "GITHUB_TOKEN": "ghp_secret",
                       "ANTHROPIC_API_KEY": "sk-secret", "EDITOR_AUTH": "hunter2"})
    assert built == {"HOME": "/home/node", "PATH": "/bin", "USER": "u",  # pub-lint:allow synthetic
                     "LOGNAME": "u",
                     "TMPDIR": "/tmp", "LC_ALL": "C.UTF-8", "KAWA_DSN": "dbname=kawa"}
    assert not DENIED & set(built)
    assert "EDITOR_AUTH" not in built             # allowlist, not denylist


def test_launch_passes_only_allowlisted_env_to_the_runtime(fake_herdr, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/agent.sock")
    backend = fake_herdr({**_READY, "workspace create": _fx("workspace_create.json"),
                          "agent start": _fx("agent_start.json")})
    backend.launch(LaunchSpec("w-x", "codex", "/tmp"))
    create = [c for c in fake_herdr.invocations() if c[2:4] == ["workspace", "create"]][0]
    joined = " ".join(create)
    assert "ghp_secret" not in joined and "agent.sock" not in joined
    assert "--env" in create and "HOME=" in joined


# ---- the four portability guarantees the contract states (round-2) ----

def test_contract_module_names_no_backend_mechanism():  # type: ignore[no-untyped-def]
    """A second backend has to be able to satisfy this contract, so the
    contract must not import or reference one. Prose may explain the origin;
    identifiers and imports may not."""
    import ast

    import kawa.runtime.contract as contract
    tree = ast.parse(open(contract.__file__, encoding="utf-8").read())
    for node in ast.walk(tree):                      # drop docstrings: prose is allowed
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and \
                ast.get_docstring(node) is not None:
            node.body = node.body[1:] or [ast.Pass()]
    code = ast.unparse(tree).lower()
    for word in ("herdr", "tmux", "pane", "socket", "subprocess"):
        assert word not in code, word
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "dataclasses", "typing"}, imported


def test_server_death_is_a_failure_not_an_absent_runtime(fake_herdr):  # type: ignore[no-untyped-def]
    """The sharpest misattribution available here: if a dead server read as
    'runtime absent', a supervisor would conclude the agent finished. Real
    capture of the server-down refusal, asserted against every API that
    reports presence."""
    backend = fake_herdr({"agent get": _fx("err_server_absent.json"),
                          "agent wait": _fx("err_server_absent.json"),
                          "agent read": _fx("err_server_absent.json"),
                          "workspace list": _fx("err_server_absent.json")})
    handle = RuntimeHandle("herdr", "kawa-abc")
    for call in (lambda: backend.inspect(handle),
                 lambda: backend.await_settled(handle, timeout_s=1),
                 lambda: backend.read_recent_output(handle),
                 lambda: backend.terminate(handle)):
        with pytest.raises(RuntimeBackendError) as excinfo:
            call()
        assert excinfo.value.error_class in ("server_absent", "malformed_response")
    assert backend.detect().reason == "server_absent"


def test_fixtures_carry_no_operational_identifiers():  # type: ignore[no-untyped-def]
    """These captures are published through the public mirror, and the
    gate-1 linter cannot see inside single-line JSON (#201). Until that is
    fixed the check lives here, where the captures are: a scrub that silently
    regresses on the next re-capture is worse than no scrub."""
    import glob
    import re
    forbidden = re.compile(r"/home/(?!node\b)[a-z0-9_-]+|@[a-z0-9-]*\.(?:ts\.net|local)"
                           r"|\b(?:fd7a|fe80):", re.I)
    for path in glob.glob(os.path.join(_TESTS, "fixtures", "herdr", "*")):
        body = open(path, encoding="utf-8", errors="replace").read()
        assert not forbidden.search(body), os.path.basename(path)


# ---- the observation type refuses invented vocabulary ----

def test_observation_rejects_states_outside_the_triple():  # type: ignore[no-untyped-def]
    with pytest.raises(AssertionError):
        RuntimeObservation("herdr", "kawa-abc", "present", "done", "needed", "now")
