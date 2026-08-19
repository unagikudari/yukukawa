"""Backend selection (#204 step 1) — which runtime, chosen without importing the rest.

#198's thesis is "stable semantics, replaceable mechanics", and #202 landed a contract
that names no mechanism. What it did not land is a way to pick one: the launcher
imported `HerdrBackend` at module scope and constructed it unconditionally. So kawa's
ability to launch work meant "herdr is installed and its server is up" — a dependency
in the only sense that matters, whatever the contract said on paper.

Three properties, and the reasons they are properties rather than conveniences:

  * **Lazy by construction.** A backend module is imported only when that backend is
    a candidate. Not because importing is expensive, but because an import is where a
    hard dependency hides: a module that raises, or that pulls in a third-party
    package at import time, makes every kawa surface depend on a runtime nobody asked
    to use. The registry stores loaders, never instances, and never imports at module
    scope.

  * **`auto` says what it picked, always.** Silent selection is the failure #204's
    review ask names: an operator who does not know which backend ran cannot read the
    result. `resolve()` returns the reason it chose, and the launcher prints it.

  * **An explicit choice never falls back.** `--backend herdr` on a node without herdr
    refuses with herdr's own reason. Falling back to something that happens to be
    installed would answer a question the operator did not ask, and would do it at the
    moment they were most specific.

A node with NO runtime installed is a supported state, not an error path: every other
kawa surface behaves identically and only `launch` refuses, with the probe results
attached so the refusal is actionable rather than a traceback.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from kawa.runtime.contract import BackendStatus, RuntimeBackend, RuntimeBackendError

# Declared resolution order for `auto`. First available wins; anything after it is
# never imported. The order is a policy statement, not a preference: herdr observes
# activity, tmux cannot (#204 step 2), so a node with both should get the backend that
# can tell "working" from "blocked".
AUTO_ORDER: tuple[str, ...] = ("herdr", "tmux")


def _load_herdr(session: str) -> RuntimeBackend:
    from kawa.runtime.herdr_backend import HerdrBackend

    return HerdrBackend(session)


def _load_tmux(session: str) -> RuntimeBackend:
    from kawa.runtime.tmux_backend import TmuxBackend

    return TmuxBackend(session)


# name -> loader. A loader imports its module INSIDE the call, so nothing here reaches
# a backend implementation until someone asks for that backend by name.
LOADERS: dict[str, Callable[[str], RuntimeBackend]] = {
    "herdr": _load_herdr,
    "tmux": _load_tmux,
}

CHOICES: tuple[str, ...] = ("auto", *sorted(LOADERS))


class NoBackendAvailable(Exception):
    """No candidate could run. Carries every probe result, because "nothing works" is
    only actionable if the operator can see WHAT was tried and why each declined."""

    def __init__(self, probes: tuple[tuple[str, BackendStatus], ...]):
        self.probes = probes
        detail = ", ".join(f"{name}:{status.reason or 'unavailable'}"
                           for name, status in probes) or "no backends registered"
        super().__init__(f"no runtime backend available ({detail})")


@dataclass(frozen=True)
class Selection:
    """A chosen backend and the sentence explaining why it was chosen."""

    backend: RuntimeBackend
    name: str
    reason: str                       # 'requested' | 'auto: first available of ...'
    probes: tuple[tuple[str, BackendStatus], ...] = ()


def available(name: str, session: str) -> tuple[RuntimeBackend | None, BackendStatus]:
    """Instantiate one backend and ask it whether it can run.

    A backend that cannot even be imported is reported as unavailable rather than
    allowed to propagate: an absent third-party package is exactly the "no runtime
    installed" state this module exists to keep survivable, and an ImportError
    escaping here would put a traceback where a named reason belongs."""
    try:
        backend = LOADERS[name](session)
    except ImportError as exc:                      # the adapter needs something absent
        return None, BackendStatus(available=False, reason="binary_absent",
                                   version=f"adapter import failed: {str(exc)[:60]}")
    try:
        return backend, backend.detect()
    except (RuntimeBackendError, OSError) as exc:
        # A TYPED refusal or an environmental failure is an answer: this backend
        # cannot run here. A bare `except Exception` was wrong — it also caught
        # AttributeError/TypeError/KeyError from inside an adapter and reported them
        # as `backend_refused`, so an adapter BUG would present as an absent runtime
        # and be routed to "install something" (#225 review round 1, finding 3). A bug
        # should be loud; only absence should be quiet.
        return backend, BackendStatus(available=False, reason="backend_refused",
                                      version=str(exc)[:80])


def resolve(requested: str, session: str) -> Selection:
    """Pick a backend. `requested` is a registered name or 'auto'.

    An explicit name is honoured or refused — never substituted. `auto` walks
    AUTO_ORDER and stops at the first available, so a backend later in the order is
    not imported at all."""
    if requested != "auto":
        if requested not in LOADERS:
            raise NoBackendAvailable(())            # argparse choices normally catch it
        backend, status = available(requested, session)
        if not status.available:
            raise NoBackendAvailable(((requested, status),))
        return Selection(backend, requested, "requested", ((requested, status),))

    probes: list[tuple[str, BackendStatus]] = []
    for name in AUTO_ORDER:
        if name not in LOADERS:
            continue
        backend, status = available(name, session)
        probes.append((name, status))
        if status.available:
            tried = " > ".join(n for n, _ in probes)
            return Selection(backend, name, f"auto: first available of {tried}",
                             tuple(probes))
    raise NoBackendAvailable(tuple(probes))


def for_teardown(name: str, session: str) -> RuntimeBackend:
    """The backend that can tear down an EXISTING runtime.

    Which backend tears a runtime down is a property of the recorded handle, not of
    whatever the operator passed on this invocation. Resolving `--backend` first and
    using that for teardown produced two reachable failures (#225 review round 1,
    finding 1):

      * a Work launched under herdr could not be terminated at all once herdr's
        server stopped, because selection refused before teardown was ever reached —
        the handle stayed recorded and unreachable, which is #210's failure arriving
        from the other side;
      * a Work launched under one backend could be handed to another, which would be
        given a token it cannot address, fail, and leave the real runtime alive.

    Deliberately does NOT require `detect().available`. That gate was wrong and the
    reasoning is the point: `detect()` measures readiness to START NEW WORK, while
    teardown needs only that the adapter can ADDRESS the runtime. A herdr server that
    is down, draining or overloaded reports unavailable while its processes keep
    running — refusing there declines to clean up exactly when cleanup matters, and it
    declines on evidence about a different question (#225 review round 2, (d)).
    `_terminate_proven` already treats an unconfirmed teardown as retention, so an
    attempt that fails is safe; an attempt not made is not.

    Raises NoBackendAvailable only when the adapter genuinely cannot be reached at
    all — no loader registered, or the module will not import. The caller must treat
    that as `cleanup_incomplete` and KEEP the handle: an unaddressable backend is the
    case where forgetting the runtime is most dangerous."""
    if name not in LOADERS:
        raise NoBackendAvailable(((name, BackendStatus(
            available=False, reason="binary_absent",
            version="no adapter registered for the recorded backend")),))
    try:
        return LOADERS[name](session)
    except ImportError as exc:
        raise NoBackendAvailable(((name, BackendStatus(
            available=False, reason="binary_absent",
            version=f"adapter import failed: {str(exc)[:60]}")),)) from exc
