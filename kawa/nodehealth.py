"""Node-local collector health — the reader that makes a status file mean something.

Every resident on a kawa node already drops a machine-readable status file in
`~/.kawa/status/`. Until now nothing read them at session start, so a failure
had no route to anyone: `kawa-goatcounter.service` failed on 2026-08-19 and
2026-08-20, silently lost a day of the site-visit series, and was found by
hand on the third day. journald had the evidence the whole time; journald is
not a reader.

This module is deliberately NOT part of `kawa.brief`. That brief is a read
over Kawa's own projections — event-sourced, replicated, the same on every
node. Local unit health is none of those things: it is one machine's opinion
about its own daemons, it is not an Event, and putting it behind the same
function would make a DB-pure read touch the filesystem. `scripts/brief.py`
composes the two; nothing else has to know.

Two signals, because they fail differently:

  * `<component>.status` — written by the collector on EVERY run including the
    failing one, carrying `ok`. Self-clearing: the next good run overwrites it.
  * `<unit>.onfail` — written by systemd's `OnFailure=` hook when the process
    died before it could write anything at all (OOM, TimeoutStartSec, a
    traceback before the status write). It cannot clear itself, so a status
    file that is newer AND ok supersedes it.

Silent when healthy. A health block that prints every session teaches the
reader to skip it, which is how the goatcounter failure would go unread a
second time."""
from __future__ import annotations

import json
import os

def status_dir() -> str:
    """Where residents leave their status, resolved per call.

    `KAWA_STATUS_DIR` exists for the same reason `KAWA_TEST_DSN_A` does: a
    module constant frozen at import is reachable from a test, and on
    2026-08-20 one was — a collector test wrote `node: "test", ok: false`
    into the operator's REAL status directory, where the session brief would
    have reported it as a live failure. The DB half of that lesson is already
    fenced in tests/conftest.py; this is the filesystem half."""
    return os.environ.get("KAWA_STATUS_DIR") or os.path.expanduser("~/.kawa/status")


STATUS_DIR = None       # sentinel: scan() resolves at call time, never at import


def _component(unit: str) -> str:
    """`kawa-goatcounter.service` -> `goatcounter`, matching the status filenames."""
    name = unit[:-len(".service")] if unit.endswith(".service") else unit
    return name[len("kawa-"):] if name.startswith("kawa-") else name


def scan(status_path: str | None = None) -> list[dict]:
    """One finding per unhealthy component. Empty list means nothing to say.

    Order is the status files' name order, then components known only by a
    marker — NOT severity. An earlier docstring claimed "worst-known-first",
    which round 2 of #228 pointed out was never true; there is no severity to
    rank by, so the claim is dropped rather than faked."""
    status_path = status_path or status_dir()
    try:
        entries = sorted(os.listdir(status_path))
    except OSError:
        return []                      # no status dir = not a kawa resident node

    ok_at: dict[str, float] = {}
    findings: list[dict] = []
    for name in entries:
        if not name.endswith(".status"):
            continue
        path = os.path.join(status_path, name)
        comp = name[: -len(".status")]
        try:
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
        except Exception as exc:
            # a status nobody can parse is not a status — say so rather than
            # skipping it, or an unreadable file reads as a healthy one
            findings.append({"component": comp, "why": f"status unreadable: {exc}"})
            continue
        if "ok" not in st:
            continue                   # older collectors predate the flag; not a failure
        if st["ok"]:
            ok_at[comp] = os.path.getmtime(path)
            continue
        why = st.get("error") or ", ".join(
            f"{f.get('day', '?')}: {f.get('error', '?')}" for f in st.get("failed", [])
        ) or "reported not ok"
        findings.append({"component": comp, "at": st.get("ts"), "why": why})

    for name in entries:
        if not name.endswith(".onfail"):
            continue
        unit = name[: -len(".onfail")]
        comp = _component(unit)
        marker = os.path.join(status_path, name)
        # a newer successful run supersedes a marker systemd cannot clear
        if ok_at.get(comp, 0.0) > os.path.getmtime(marker):
            continue
        try:
            with open(marker, encoding="utf-8") as f:
                at = json.load(f).get("ts")
        except Exception:
            at = None
        findings.append({"component": comp, "at": at,
                         "why": f"unit failed — systemctl --user status {unit}"})

    # One event, one line. A day-level failure inside a collector writes
    # `ok:false` AND exits nonzero, which fires OnFailure — so the same failure
    # arrives twice under the same component key (round-1 review of #228,
    # finding 3). The two-signal design exists for the crash-before-write case,
    # not to say the same thing twice: merge per component, status reason first
    # because it carries the detail.
    merged: dict[str, dict] = {}
    for f in findings:
        seen = merged.get(f["component"])
        if seen is None:
            merged[f["component"]] = dict(f)
            continue
        seen["why"] = f"{seen['why']}; also {f['why']}"
        # ISO8601-Z sorts lexicographically; show the MOST RECENT of the two,
        # or a stale status timestamp would date a fresher unit failure
        stamps = [x for x in (seen.get("at"), f.get("at")) if x]
        seen["at"] = max(stamps) if stamps else None
    return list(merged.values())


def render(findings: list[dict]) -> str:
    """A block for the session brief, or '' when there is nothing wrong."""
    if not findings:
        return ""
    lines = ["Node-local residents needing attention "
             "(this machine only — not Kawa state):"]
    for f in findings:
        when = f" [{f['at']}]" if f.get("at") else ""
        lines.append(f"  {f['component']}{when} — {f['why']}")
    return "\n".join(lines)
