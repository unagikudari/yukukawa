"""Start a runtime for one actionable Work (#200 rev 3 §REAL-3, checkpoint 2).

    KAWA_DSN=dbname=kawa python scripts/runtime_launch.py <work_ref> \
        --session <runtime-session> [--agent-kind codex|claude] [--cwd PATH]
        [--force] [--terminate]

Operator-invoked in H1: the supervisor does not call this (that is H2, where
occupancy leases exist). What it does, in order, is the whole point:

  1. **Refuse unless the Work is actually actionable** — read from the
     projections, not from the operator's belief about them.
  2. **Refuse a duplicate** — under an exclusive lock, so two operators
     racing one Work cannot both pass the check (§3a). Unknown liveness
     refuses; only `--force` overrides, and it says what it overrode.
  3. **Launch, then gate** — the runtime exists before it is ready, so a
     wedge is addressable. A runtime still `blocked` after launch is
     `blocked_at_launch`: it is torn down before the error is reported, or it
     would sit there absorbing the next attempt's cue. **Deregistration
     requires PROOF of absence** — a termination that could not be confirmed
     keeps the handle and says `cleanup_incomplete`, because forgetting a
     runtime that is still alive is how one Work gets two of them (#210).
  4. **Wake with the constant, verify the echo** — the cue is
     `kawa.runtime.wake.WAKE_CUE` and nothing else can reach the PTY: this
     module never formats, appends to, or derives a prompt. A cue that never
     appears in the runtime's own output is `wake_echo_missing` (the boot
     race measured in the H0 probe), and a runtime that goes `blocked` after
     the cue is reported too.
  5. **Write telemetry where it cannot become truth** — an overwrite-only
     status file for humans. Not the event log, not a projection, not
     occupancy: this script writes NOTHING to kawa. The launched agent
     records its own Results, having pulled its own orientation.

Exit codes: 0 launched (or terminated), 2 refused with a named reason.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import re
import sys
import tempfile
import time

import psycopg

from kawa import nodehealth
from kawa.runtime.contract import LaunchSpec, RuntimeBackendError, RuntimeHandle
from kawa.runtime.handles import locked
from kawa.runtime.registry import CHOICES, NoBackendAvailable, for_teardown, resolve
from kawa.runtime.wake import WAKE_CUE

def status_file() -> str:
    return os.path.join(nodehealth.status_dir(), "runtime.status")


STATUS_FILE = None   # sentinel: resolved per call (kawa.nodehealth.status_dir)
_ACTIONABLE = ("ready",)
_LAUNCH_SETTLE_S = 90.0
_WAKE_SETTLE_S = 60.0
_WAKE_DEADLINE_S = 90.0      # total budget for proving the cue landed
_WAKE_RETRY_S = 5.0


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Refused(Exception):
    """A refusal the operator should read, with a named reason."""


def work_is_actionable(conn: psycopg.Connection, work_ref: str) -> str:
    """The projection answers, not the caller. Returns the execution state."""
    with conn.cursor() as cur:
        cur.execute("SELECT execution FROM current_work WHERE work_ref = %s", (work_ref,))
        row = cur.fetchone()
    if not row:
        raise Refused(f"no such Work: {work_ref}")
    if row[0] not in _ACTIONABLE:
        raise Refused(f"Work is {row[0]}, not actionable ({'/'.join(_ACTIONABLE)})")
    return row[0]


def launch(backend, conn, work_ref: str, *, agent_kind: str, cwd: str,
           force: bool = False, session: str = "kawa", loader=for_teardown) -> RuntimeHandle:
    """Steps 1-4. The lock spans the duplicate check AND the launch, so a
    second caller cannot slip between them (§3a).

    `loader` resolves the backend that owns an ALREADY-RECORDED runtime, which is not
    necessarily the one being launched — see `terminate`. It is a parameter because
    the recorded backend is a genuine second resolution point, and because the
    launcher's own tests must be able to drive it without a registered adapter."""
    work_is_actionable(conn, work_ref)
    status = backend.detect()
    if not status.available:
        raise Refused(f"runtime unavailable: {status.reason}")

    with locked() as cache:
        existing = cache.get(work_ref)
        if existing and not force:
            handle = RuntimeHandle(existing["backend"], existing["token"])
            # The RECORDED backend answers the liveness question, never the one being
            # launched. Asking the new backend about a foreign token is the worst
            # reachable outcome in this file: `tmux.inspect(herdr-token)` finds no tmux
            # session, reports `absent`, the entry is dropped as stale, and a second
            # runtime starts while the first keeps running with its handle lost
            # forever. #225 round 1 fixed --force and --terminate and left this path,
            # which is where the guard actually lives (#225 round 2, finding 1).
            try:
                prior = loader(existing["backend"], session)
            except NoBackendAvailable as exc:
                raise Refused(
                    f"a runtime may still be running for {work_ref} under "
                    f"{existing['backend']}, which cannot be reached here ({exc}); "
                    "liveness is unknown, so this refuses rather than guessing") from exc
            try:
                observation = prior.inspect(handle)
            except RuntimeBackendError as exc:
                # unknown liveness is NOT absence: refusing costs a --force,
                # guessing costs a second live agent on the same Work
                raise Refused(f"a runtime may still be running for {work_ref} "
                              f"(liveness unknown: {exc.error_class}); "
                              "re-run with --force if you have checked")
            if observation.presence == "present":
                raise Refused(f"a runtime is already running for {work_ref} "
                              f"(activity={observation.activity}); "
                              "use --terminate first, or --force")
            cache.drop(work_ref)                    # stale entry, runtime is gone
        elif existing and force:
            # --force overrides the operator's *uncertainty*, never the
            # requirement for proof. If the previous runtime cannot be shown to
            # be gone, starting a replacement is precisely the duplicate this
            # guard exists to prevent — force must escalate, not manufacture
            # a second runtime (#210).
            print(f"[launch] --force: replacing recorded runtime for {work_ref}",
                  file=sys.stderr)
            # the RECORDED backend tears down the recorded runtime, even when this
            # invocation is launching under a different one (#225 round 1, finding 1)
            try:
                prior = loader(existing["backend"], session)
            except NoBackendAvailable as exc:
                _report_cleanup_incomplete(work_ref)
                raise Refused(
                    f"--force cannot reach the backend that owns the recorded runtime "
                    f"for {work_ref} ({exc}); refusing to start a second one.") from exc
            if not _terminate_proven(prior, RuntimeHandle(existing["backend"],
                                                          existing["token"])):
                _report_cleanup_incomplete(work_ref)
                raise Refused(
                    f"--force could not prove the recorded runtime for {work_ref} "
                    "is gone (cleanup_incomplete); refusing to start a second one. "
                    "Terminate it yourself, then re-run.")
            cache.drop(work_ref)

        handle = backend.launch(LaunchSpec(work_ref, agent_kind, cwd))
        cache.put(work_ref, backend=handle.backend, token=handle.token,
                  started_at=_now())

    try:
        settled = backend.await_settled(handle, timeout_s=_LAUNCH_SETTLE_S)
        if settled.activity == "blocked":
            raise RuntimeBackendError(backend.name, "blocked_at_launch",
                                      "runtime stopped for input before it was woken")
        deliver_wake(backend, handle)
    except RuntimeBackendError:
        if not _cleanup(backend, handle, work_ref):  # never leave a wedge behind —
            _report_cleanup_incomplete(work_ref)     # and never forget one either
        raise
    write_status(work_ref, backend.inspect(handle))
    return handle


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _flatten(text: str) -> str:
    """Terminal output is WRAPPED: a pane re-flows a submitted line across
    several rows with its own indentation (measured in the H0 probe), so a
    naive substring match against the cue fails on a cue that did land.
    Collapsing whitespace compares what was written, not how it was drawn.

    Escape sequences are stripped first. The runtime measured here returns
    already-rendered text with none in it, but that is its choice, not a
    contract — and a colourised echo that failed to match would look exactly
    like a cue that never arrived, which is the one mistake this gate must
    not make."""
    return " ".join(_ANSI.sub("", text).split())


def deliver_wake(backend, handle: RuntimeHandle) -> None:
    """Step 4. The cue is a constant this function neither builds nor
    decorates — there is deliberately no parameter to pass one in.

    Delivery is retried until the runtime's own output shows the cue, or the
    deadline passes. Retrying a message is normally a way to duplicate work;
    here it is safe precisely BECAUSE the cue carries no content — it says
    "look", so saying it twice asks an agent to look twice and can duplicate
    nothing. A content-carrying prompt could not be retried this way, which
    is one more reason the cue stays empty.

    Retry is also the only correct mechanism available: measured live on
    2026-08-18, a runtime reports `interactive_ready` and settles to a
    quiescent state while its REPL is still coming up, then accepts and
    silently drops the submission. No readiness signal the runtime offers
    can be trusted, so delivery is proven by observation instead."""
    marker = _flatten(WAKE_CUE.split(";")[0])       # distinctive, survives wrapping
    # The baseline is taken ONCE, before anything is sent. Comparing each
    # attempt against the previous one instead would misjudge the case where
    # attempt N's echo only rendered after its check: attempt N+1 would then
    # see the marker in its own "before" and demand further change from a
    # runtime that had already answered.
    baseline = _flatten(backend.read_recent_output(handle))
    deadline = time.monotonic() + _WAKE_DEADLINE_S
    attempt = 0
    while True:
        attempt += 1
        backend.wake(handle, WAKE_CUE)
        observation = backend.await_settled(handle, timeout_s=_WAKE_SETTLE_S)
        after = _flatten(backend.read_recent_output(handle))
        # only the runtime's own output can say the cue landed — and if the
        # marker was somehow on screen before we sent anything, the screen
        # must also have changed, so a leftover cannot vouch for this cue
        if marker in after and (marker not in baseline or after != baseline):
            break
        if time.monotonic() >= deadline:
            raise RuntimeBackendError(
                backend.name, "wake_echo_missing",
                f"the cue never appeared in the runtime's output ({attempt} attempts)")
        time.sleep(_WAKE_RETRY_S)
    if observation.activity == "blocked":
        raise RuntimeBackendError(backend.name, "blocked_at_launch",
                                  "runtime stopped for input after the cue")


def terminate(work_ref: str, session: str, *, loader=for_teardown) -> str:
    """Operator teardown. Same rule as every other exit: the entry goes only
    when the runtime is proven gone, so an unreachable backend leaves the
    locator in place instead of quietly losing it (#210).

    The backend comes from the RECORDED ENTRY, never from `--backend`: a runtime is
    torn down by the thing that created it. Nothing is resolved when there is no
    entry, so terminating an unknown Work answers on a node with no runtime at all
    rather than refusing to look (#225 review round 1, finding 1).

    Returns one of three OUTCOMES rather than a bool. "Nothing to clean up" and
    "there is still a runtime and I could not kill it" were both `false`, printed
    identically and exited 0 — and that difference is the whole of #210. A caller
    that cannot tell them apart has to parse stderr prose to find out whether a live
    agent is loose (#225 review round 2, finding 2).

      no_runtime          — no entry; nothing existed to tear down
      terminated          — proven absent, entry dropped
      cleanup_incomplete  — entry RETAINED; a runtime may still be alive
    """
    with locked() as cache:                          # read under the lock, decide outside
        entry = cache.get(work_ref)
    if not entry:
        return "no_runtime"

    # Resolution and the teardown probe happen OUTSIDE the lock. `launch` already
    # detects before locking for the same reason: a backend probe can hang on a
    # socket, and holding the cache lock across it blocks every other invocation on
    # this node — including the duplicate check that #210's guarantee rests on
    # (#225 review round 2, (a)).
    try:
        backend = loader(entry["backend"], session)
    except NoBackendAvailable:
        _report_cleanup_incomplete(work_ref)         # handle RETAINED — see #210
        return "cleanup_incomplete"
    if not _terminate_proven(backend,
                             RuntimeHandle(entry["backend"], entry["token"])):
        _report_cleanup_incomplete(work_ref)
        return "cleanup_incomplete"

    with locked() as cache:
        # Compare-and-drop on the token. Moving the probe outside the lock (round 2)
        # opened a window: a launch can observe the runtime we just killed, decide the
        # entry is stale, and record a NEW runtime before we re-take the lock. An
        # unconditional drop would then erase the new runtime's handle while it keeps
        # running — the orphan that makes the next launch start a duplicate, which is
        # #210 reached by a different road (#225 review round 3).
        #
        # Dropping only OUR token is the whole fix: this call proved one runtime gone
        # and may retire exactly that one.
        current = cache.get(work_ref)
        if current is None or current.get("token") != entry["token"]:
            return "terminated"                      # ours is gone; someone else owns
        cache.drop(work_ref)                         # the entry now, and it stays
    return "terminated"


def _cleanup(backend, handle: RuntimeHandle, work_ref: str) -> bool:
    """Tear down after a failed launch. True when the runtime was PROVEN absent
    and the handle was dropped; False when the handle was RETAINED because
    absence could not be proven (#210).

    The old shape swallowed a failed `terminate` and dropped the entry anyway,
    which is how a recoverable cleanup failure became an undetectable duplicate:
    the runtime survived, kawa deleted the only thing pointing at it, and the
    next launch — seeing nothing recorded — started a second one on the same
    Work. The handle is non-authoritative telemetry, but it is the ONLY H1
    mechanism standing between one Work and two live runtimes."""
    if not _terminate_proven(backend, handle):
        return False
    with locked() as cache:
        cache.drop(work_ref)
    return True


def _terminate_proven(backend, handle: RuntimeHandle) -> bool:
    """True only when the runtime is proven GONE.

    Termination attempted is not runtime absent. A clean return from
    `terminate` is proof, because the contract requires cleanup to be
    idempotent and to report a partial teardown as `terminate_failed` rather
    than a clean exit it did not achieve — so a backend whose primitive is not
    conclusive must raise, which routes it here into the confirming inspect.
    An inspect that cannot answer is not absence either."""
    try:
        backend.terminate(handle)
        return True
    except RuntimeBackendError:
        pass
    try:
        return backend.inspect(handle).presence == "absent"
    except RuntimeBackendError:
        return False


def _report_cleanup_incomplete(work_ref: str) -> None:
    """Surface it. The original launch failure still leads, but a retained
    handle whose runtime may be alive is a condition an operator has to see —
    swallowing it is what made this defect invisible for as long as it was."""
    print(f"[launch] cleanup_incomplete: could not prove the runtime for "
          f"{work_ref} is gone; its handle is RETAINED. Run --terminate, or "
          f"check it before using --force.", file=sys.stderr)


def write_status(work_ref: str, observation) -> None:
    """Operator display ONLY (§REAL-4). Overwrite-only, no history, and
    nothing in kawa reads it — Work standing comes from the log."""
    path = status_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "_contract": "runtime telemetry for humans; never an input to Work state",
        "generated_at": _now(),
        "runtimes": {work_ref: observation.to_dict()},
    }
    previous = {}
    try:
        with open(path, encoding="utf-8") as fh:
            previous = (json.load(fh) or {}).get("runtimes", {})
    except (OSError, ValueError):
        pass
    payload["runtimes"] = {**previous, **payload["runtimes"]}
    # atomic swap like the handle cache: an operator dashboard polling this
    # file must never catch it half-written (review finding 3)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_ref")
    parser.add_argument("--session", default=os.environ.get("KAWA_RUNTIME_SESSION", "kawa"))
    parser.add_argument("--agent-kind", default="codex", choices=("codex", "claude"))
    parser.add_argument("--cwd", default=os.path.expanduser("~/kawa"))
    parser.add_argument("--force", action="store_true",
                        help="replace a recorded runtime for this Work")
    parser.add_argument("--terminate", action="store_true",
                        help="tear the recorded runtime down instead of launching")
    parser.add_argument("--backend", default="auto", choices=CHOICES,
                        help="which runtime to use; 'auto' takes the first available "
                             "in the declared order and says which it took")
    args = parser.parse_args(argv)

    # Teardown never consults `--backend`: the recorded entry names the backend that
    # owns the runtime, and asking selection first meant a Work launched under a
    # backend that later went down could not be terminated at all (#225 round 1,
    # finding 1). Nothing is resolved here — an unknown Work answers `false` even on
    # a node with no runtime installed.
    if args.terminate:
        outcome = terminate(args.work_ref, args.session)
        payload = {"work_ref": args.work_ref, "outcome": outcome,
                   "terminated": outcome == "terminated"}
        if outcome == "cleanup_incomplete":
            # exit 2, like every other refusal: a retained handle means a runtime may
            # still be alive, and that must not read as success to a caller that only
            # checks the status code
            print(json.dumps(payload, indent=2), file=sys.stderr)
            return 2
        print(json.dumps(payload, indent=2))
        return 0

    # For a LAUNCH the operator's choice governs, and a node with no runtime installed
    # refuses here with the probe results rather than raising an ImportError from a
    # module-scope import — which is what made "kawa can launch work" mean "herdr is
    # up" no matter what the contract said (#204 step 1).
    try:
        selection = resolve(args.backend, args.session)
    except NoBackendAvailable as exc:
        print(json.dumps({"work_ref": args.work_ref, "refused": str(exc),
                          "probed": {name: status.reason or "available"
                                     for name, status in exc.probes}}, indent=2),
              file=sys.stderr)
        return 2
    backend = selection.backend

    with psycopg.connect(os.environ.get("KAWA_DSN", "dbname=kawa")) as conn:
        try:
            handle = launch(backend, conn, args.work_ref, session=args.session,
                            agent_kind=args.agent_kind, cwd=args.cwd, force=args.force)
        except Refused as exc:
            print(json.dumps({"work_ref": args.work_ref, "refused": str(exc)}, indent=2),
                  file=sys.stderr)
            return 2
        except RuntimeBackendError as exc:
            print(json.dumps({"work_ref": args.work_ref, "failed": exc.error_class,
                              "detail": exc.detail}, indent=2), file=sys.stderr)
            return 2
    print(json.dumps({"work_ref": args.work_ref, "backend": handle.backend,
                      "backend_selected_by": selection.reason,
                      # every candidate and why it declined — an operator reading a
                      # result must be able to see WHY the earlier ones were skipped,
                      # not just which one won (#225 review round 1, finding 2)
                      "backend_probes": {name: status.reason or "available"
                                         for name, status in selection.probes},
                      "launched": True, "woken": True}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
