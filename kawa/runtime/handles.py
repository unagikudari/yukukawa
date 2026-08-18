"""Handle cache — which Work has a live runtime on THIS node (#200 rev 3 §3a).

H1 launches before lease semantics exist (occupancy is #186 / H2), so the
window where two operators start two agents on one Work is real. This cache
closes it on one node, and closes it the only way a file can: an exclusive
`flock` held across the whole check → liveness → launch → record sequence.
Atomic writes alone would not — both callers would read "no handle", both
would launch, and the loser's runtime would be orphaned with nothing
pointing at it (round-1 review F7, round-2 F2a).

What this is NOT: a source of truth. It records that a runtime was started,
never that Work is claimed, owned, done or in progress. Nothing may read it
to decide Work standing — kawa's log answers that. If the file is deleted,
the only consequence is that a duplicate launch is no longer refused; no
Kawa state is lost, because none of it was here.

Liveness is re-checked through the backend on every read, so a stale entry
(the runtime died, the server was restarted) never blocks a relaunch. A
backend that CANNOT answer is not treated as absence: unknown liveness
refuses the launch instead of guessing, and `--force` is the operator's way
to say they looked.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from typing import Iterator

DEFAULT_PATH = "~/.kawa/runtime/handles.json"

# the only keys a cache entry may carry: enough to find and terminate a
# runtime, nothing that could be mistaken for Work state
ENTRY_FIELDS = ("backend", "token", "started_at")


def cache_path() -> str:
    return os.path.expanduser(os.environ.get("KAWA_RUNTIME_HANDLES") or DEFAULT_PATH)


@contextlib.contextmanager
def locked(path: str | None = None) -> Iterator["HandleCache"]:
    """Hold the exclusive lock for the whole critical section."""
    target = path or cache_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    lock_path = target + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield HandleCache(target)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class HandleCache:
    """Only reachable inside `locked()` — every mutation happens under the
    lock by construction rather than by remembering to take it."""

    def __init__(self, path: str) -> None:
        self.path = path

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        directory = os.path.dirname(self.path)
        fd, tmp = tempfile.mkstemp(dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)          # atomic swap, never a torn file
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def get(self, work_ref: str) -> dict | None:
        entry = self._load().get(work_ref)
        return entry if isinstance(entry, dict) else None

    def put(self, work_ref: str, *, backend: str, token: str, started_at: str) -> None:
        data = self._load()
        data[work_ref] = {"backend": backend, "token": token, "started_at": started_at}
        self._save(data)

    def drop(self, work_ref: str) -> None:
        data = self._load()
        if data.pop(work_ref, None) is not None:
            self._save(data)

    def items(self) -> dict:
        return self._load()
