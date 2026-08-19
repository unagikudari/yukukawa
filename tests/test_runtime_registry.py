"""Backend selection (#204 step 1).

The guarantee under test is not "selection works" but "no runtime is a dependency".
That is a property of what gets IMPORTED and what happens when nothing is installed,
so these tests watch imports and force absence rather than exercising a happy path.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from kawa.runtime.contract import BackendStatus
from kawa.runtime import registry


class _Recorder:
    """A loader that records whether it was ever reached."""

    def __init__(self, name, available, reason="", raises=None):
        self.name, self.calls, self._raises = name, 0, raises
        self._status = BackendStatus(available=available, reason=reason)

    def __call__(self, session):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return _Backend(self.name, self._status)


class _Backend:
    def __init__(self, name, status):
        self.name, self._status = name, status

    def detect(self):
        if isinstance(self._status, Exception):
            raise self._status
        return self._status


@pytest.fixture()
def registered(monkeypatch):  # type: ignore[no-untyped-def]
    """Install a synthetic registry so the tests do not depend on what this node
    happens to have installed — which is the same reason the feature exists."""
    def _install(**loaders):
        monkeypatch.setattr(registry, "LOADERS", dict(loaders))
        monkeypatch.setattr(registry, "AUTO_ORDER", tuple(loaders))
        return loaders
    return _install


def test_importing_the_registry_does_not_import_any_backend() -> None:
    """The property that makes "no runtime installed" survivable.

    A module-scope `from kawa.runtime.herdr_backend import HerdrBackend` in the
    launcher is what made kawa's ability to launch work mean "herdr is up", whatever
    the contract said. Asserted in a SUBPROCESS: this test session has already
    imported half the runtime package, so checking `sys.modules` in-process would
    pass on someone else's import."""
    probe = ("import sys; import kawa.runtime.registry as r; "
             "assert 'kawa.runtime.herdr_backend' not in sys.modules, sorted("
             "m for m in sys.modules if 'herdr' in m); "
             "assert r.LOADERS; print('clean')")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "clean"


def test_auto_stops_at_the_first_available_and_never_loads_the_rest(registered) -> None:  # type: ignore[no-untyped-def]
    first = _Recorder("first", available=True)
    second = _Recorder("second", available=True)
    registered(first=first, second=second)

    selection = registry.resolve("auto", "s")
    assert selection.name == "first"
    assert second.calls == 0, "a backend after the chosen one must not be loaded"


def test_auto_says_which_backend_it_took(registered) -> None:  # type: ignore[no-untyped-def]
    """Silent selection is the operator-surprise risk #204 names: a result you cannot
    read because you do not know what produced it."""
    registered(absent=_Recorder("absent", available=False, reason="binary_absent"),
               present=_Recorder("present", available=True))

    selection = registry.resolve("auto", "s")
    assert selection.name == "present"
    assert "absent" in selection.reason and "present" in selection.reason
    assert dict(selection.probes)["absent"].reason == "binary_absent"


def test_an_explicit_choice_is_refused_rather_than_substituted(registered) -> None:  # type: ignore[no-untyped-def]
    """Falling back would answer a question the operator did not ask, at the moment
    they were most specific."""
    other = _Recorder("other", available=True)
    registered(wanted=_Recorder("wanted", available=False, reason="server_absent"),
               other=other)

    with pytest.raises(registry.NoBackendAvailable) as exc:
        registry.resolve("wanted", "s")
    assert "server_absent" in str(exc.value)
    assert other.calls == 0, "an explicit refusal must not probe an alternative"


def test_no_backend_available_carries_every_probe(registered) -> None:  # type: ignore[no-untyped-def]
    """"Nothing works" is only actionable if the operator can see what was tried."""
    registered(a=_Recorder("a", available=False, reason="binary_absent"),
               b=_Recorder("b", available=False, reason="server_absent"))

    with pytest.raises(registry.NoBackendAvailable) as exc:
        registry.resolve("auto", "s")
    assert dict(exc.value.probes).keys() == {"a", "b"}
    assert "binary_absent" in str(exc.value) and "server_absent" in str(exc.value)


def test_an_adapter_that_cannot_import_is_a_reason_not_a_traceback(registered) -> None:  # type: ignore[no-untyped-def]
    """An absent third-party package IS the "no runtime installed" state. Letting the
    ImportError escape would put a traceback where a named reason belongs."""
    registered(broken=_Recorder("broken", available=True,
                                raises=ImportError("no module named 'herdr_sdk'")))

    with pytest.raises(registry.NoBackendAvailable) as exc:
        registry.resolve("auto", "s")
    assert dict(exc.value.probes)["broken"].reason == "binary_absent"


def test_a_typed_or_environmental_detect_failure_is_a_no(registered) -> None:  # type: ignore[no-untyped-def]
    """A backend whose probe refuses in the contract's own vocabulary, or fails on
    the environment, has answered the question: it cannot run here."""
    from kawa.runtime.contract import RuntimeBackendError

    for raised in (RuntimeBackendError("x", "server_absent"), OSError("socket refused")):
        class _Exploding(_Recorder):
            def __call__(self, session, _r=raised):
                self.calls += 1
                return _Backend("exploding", _r)

        registered(exploding=_Exploding("exploding", available=False))
        with pytest.raises(registry.NoBackendAvailable) as exc:
            registry.resolve("auto", "s")
        assert dict(exc.value.probes)["exploding"].reason == "backend_refused"


def test_an_adapter_bug_is_loud_rather_than_reported_as_an_absent_runtime(registered) -> None:  # type: ignore[no-untyped-def]
    """The boundary that a bare `except Exception` erased.

    An AttributeError inside an adapter is a BUG. Reporting it as `backend_refused`
    routes the operator to "install something", which will not help and hides the
    defect — absence should be quiet, a bug should not be (#225 review round 1,
    finding 3)."""
    class _Buggy(_Recorder):
        def __call__(self, session):
            self.calls += 1
            return _Backend("buggy", AttributeError("'NoneType' has no attribute 'x'"))

    registered(buggy=_Buggy("buggy", available=False))
    with pytest.raises(AttributeError):
        registry.resolve("auto", "s")


_BLOCK = """
import sys, types

class _Block:
    '''Make the adapter module genuinely unimportable — not absent from sys.modules,
    but impossible to load, which is the state of a node that never installed it.'''
    def find_module(self, name, path=None):
        return self if name == "kawa.runtime.herdr_backend" else None
    def find_spec(self, name, path=None, target=None):
        if name == "kawa.runtime.herdr_backend":
            raise ImportError("blocked: no runtime installed on this node")
        return None

sys.meta_path.insert(0, _Block())
"""


def test_every_kawa_surface_works_with_no_runtime_installed() -> None:
    """The actual claim of #204: kawa must not be dependent on herdr.

    Not "the registry handles absence" — that is a unit test. This asserts the whole
    package still imports and the launcher still ANSWERS on a node where the adapter
    cannot be loaded at all, which is what an operator who never installed a runtime
    has. Run in a subprocess with the module blocked at the meta-path, because a
    dependency you can only observe at import time cannot be observed from inside a
    session that has already imported it."""
    probe = _BLOCK + """
import kawa.retrieval, kawa.application.services, kawa.projections.reducers
import kawa.runtime.contract, kawa.runtime.handles, kawa.runtime.wake
from kawa.runtime.registry import resolve, NoBackendAvailable
try:
    resolve("auto", "s")
    print("UNEXPECTED: a blocked adapter reported itself available")
except NoBackendAvailable as exc:
    # binary_absent specifically, NOT server_absent: on a node where the runtime is
    # installed but its server is down, `auto` refuses either way, so asserting only
    # that it refused would pass without the block ever taking effect.
    assert dict(exc.probes)["herdr"].reason == "binary_absent", exc.probes
    print("refused-cleanly")
"""
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "refused-cleanly", out.stdout


def test_the_launcher_exits_two_with_a_named_reason_when_nothing_is_installed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Exit 2 and a reason, never a traceback — the difference between a refusal an
    operator can act on and a crash they have to decode."""
    probe = _BLOCK + """
import json, sys, io, contextlib, runpy, os
sys.argv = ["runtime_launch.py", "w-anything"]
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        runpy.run_path("scripts/runtime_launch.py", run_name="__main__")
except SystemExit as exc:
    payload = json.loads(err.getvalue())
    assert exc.code == 2, exc.code
    assert "refused" in payload and "herdr" in payload["refused"], payload
    assert payload["probed"]["herdr"] == "binary_absent", payload
    print("refused:" + payload["probed"]["herdr"])
"""
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr + out.stdout
    assert out.stdout.startswith("refused:"), out.stdout
