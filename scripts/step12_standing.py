"""step-12 durability standing — the machine check behind w-step12-durability-dogfood's Result.

Read-only. Computes, from the dogfood log + local status files + systemd, whether the R1
completion clock (#129: ">=7 consecutive days of real operation through this loop,
Observations in the log") is satisfied, and names every gap when it is not. The Result for
`w-step12-durability-dogfood` must cite this script's verdict — the acceptance line
"N consecutive scheduled restore-proofs recorded as Observations without human triggering"
is a COUNT over the log, so a human narrative alone cannot close it.

Honest limits (named, not hidden — #129 rev 3 discipline):
- supervisor CONTINUITY over the window comes from systemd (ActiveEnterTimestamp /
  NRestarts), not the log: the log records surfacing, which is sparse by design. Restarts
  do not break standing (restartable-and-idempotent is itself an acceptance criterion);
  a STALE status file does.
- replica-side evidence (replication_frontier_lag / admission rejects) lives in the
  REPLICA's local log (12B policy, one-way pull) — from this node it arrives only through
  the drill-2 Result, so the drill-2 Work being finished is the gate this checker can see.
- the propagation gate is presence-of-one-within-bound, exactly the pinned acceptance
  ("measured end-to-end ONCE, recorded"): a Work already eligible before the supervisor
  first started surfaces with a large backlog value that is a startup artifact, not an
  SLA breach, so all-must-pass would fail forever on honest data (review b5d0a8bc F2
  refuted against #129 R2). Continuous SLA watching is #126 territory, not this gate.

Usage:  KAWA_DSN=dbname=kawa .venv/bin/python scripts/step12_standing.py [--json]
Exit:   0 = standing (Result may be recorded) · 1 = not yet (gaps listed) · 2 = error
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

REQUIRED_DAYS = 7            # R1: >=7 consecutive days of real operation
PROPAGATION_BOUND_S = 65.0   # R2: T + 5s with T=60s
SUPERVISOR_STALE_S = 120     # durability-policy: 2 x tick
DRILL2_WORK = "w-step12-drill2-replica-kill"
STATUS_FILE = os.path.expanduser("~/.kawa/status/supervisor.status")


def archive_streak(rows: list[tuple[dt.date, bool, bool, str | None]],
                   as_of_date: dt.date) -> tuple[int, list[str]]:
    """Consecutive OK days ending at the LATEST proof day, ANCHORED to as_of_date: a streak
    whose latest day is older than yesterday is a dead loop, not standing (review b5d0a8bc
    F1). rows = (local_date, ok, signed, policy_digest) ascending. A missed day, a false
    proof, an unsigned proof, a MISSING digest, or a digest differing from the latest run's
    breaks it (a digest change splits the measurement lineage by design — the streak
    restarts under the new policy)."""
    gaps: list[str] = []
    if not rows:
        return 0, ["no archive_restore_proof observations in the log"]
    by_day: dict[dt.date, tuple[bool, bool, str | None]] = {}
    for day, ok, signed, digest in rows:            # latest run of a day wins (rerun-safe)
        by_day[day] = (ok, signed, digest)
    last_day = max(by_day)
    age = (as_of_date - last_day).days
    if age > 1:                                     # today may simply not have run yet
        gaps.append(f"latest restore-proof is {age} days old ({last_day}) — the archive "
                    "loop is not running")
    current_digest = by_day[last_day][2]
    streak = 0
    day = last_day
    while day in by_day:
        ok, signed, digest = by_day[day]
        if not ok:
            gaps.append(f"{day}: restore-proof FAILED (loud path) — streak restarts after it")
            break
        if not signed:
            gaps.append(f"{day}: restore-proof unsigned — does not count toward standing")
            break
        if not digest:                              # review b5d0a8bc F4: None must not
            gaps.append(f"{day}: no policy digest in source_revision — lineage unverifiable")
            break                                   # silently equal another None
        if digest != current_digest:
            gaps.append(f"{day}: policy digest changed ({digest} != {current_digest}) — lineage split")
            break
        streak += 1
        day -= dt.timedelta(days=1)
    return streak, gaps


def read_supervisor_status(status_file: str, now: dt.datetime) -> tuple[bool, str | None]:
    """(fresh, gap) — fresh means ok:true and within the 2x-tick staleness bound."""
    try:
        with open(status_file, encoding="utf-8") as f:
            st = json.load(f)
        age = (now - dt.datetime.strptime(st["ts"], "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=dt.timezone.utc)).total_seconds()
        if bool(st.get("ok")) and age <= SUPERVISOR_STALE_S:
            return True, None
        return False, f"supervisor status stale/not-ok (age {int(age)}s, ok={st.get('ok')})"
    except (OSError, ValueError, KeyError) as exc:
        return False, f"supervisor status unreadable: {exc}"


def assemble(proofs: list[tuple[dt.datetime, bool, bool, str | None]],
             surfaced: list[tuple[dt.datetime, float | None]],
             drill2_execution: str | None,
             supervisor: tuple[bool, str | None],
             now: dt.datetime) -> dict:
    """Pure assembly of the verdict from fetched rows — separately testable end to end.
    Day bucketing happens HERE via astimezone() (the node's local calendar, i.e. the same
    clock the systemd timer fires on) — never via a ::date cast whose result depends on
    the ambient postgres session TimeZone (review b5d0a8bc F3)."""
    gaps: list[str] = []
    local_today = now.astimezone().date()

    proof_days = [(ts.astimezone().date(), ok, signed, digest)
                  for ts, ok, signed, digest in proofs]
    streak, streak_gaps = archive_streak(proof_days, as_of_date=local_today)
    gaps += streak_gaps
    if streak < REQUIRED_DAYS:
        gaps.append(f"archive restore-proof streak {streak}/{REQUIRED_DAYS} days")

    clock_start = min((ts for ts, _ in surfaced), default=None)
    if clock_start is None:
        days_elapsed = 0
        earliest = None
        gaps.append("no work_surfaced observation — the R1 clock has not started")
    else:
        days_elapsed = (now - clock_start).days
        earliest = (clock_start + dt.timedelta(days=REQUIRED_DAYS)).date()
        if days_elapsed < REQUIRED_DAYS:
            gaps.append(f"R1 clock {days_elapsed}/{REQUIRED_DAYS} days (earliest Result {earliest})")

    # presence-of-one within bound — the pinned "measured end-to-end once" acceptance;
    # see the module docstring for why all-must-pass is wrong on honest data
    if not any(v is not None and v <= PROPAGATION_BOUND_S for _, v in surfaced):
        gaps.append(f"no work_surfaced measurement within the {PROPAGATION_BOUND_S:.0f}s "
                    "bound (12C acceptance)")

    supervisor_fresh, sup_gap = supervisor
    if sup_gap:
        gaps.append(sup_gap)

    if drill2_execution != "finished":
        gaps.append(f"{DRILL2_WORK} execution={drill2_execution} — replica-side evidence "
                    "(frontier-lag gap + catch-up + F5 numbers) arrives through its Result")

    return {
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "standing": not gaps,
        "archive_streak_days": streak,
        "clock_start": str(clock_start)[:19] if clock_start else None,
        "days_elapsed": days_elapsed,
        "earliest_result_date": str(earliest) if earliest else None,
        "supervisor_fresh": supervisor_fresh,
        "drill2_execution": drill2_execution,
        "gaps": gaps,
    }


def compute(conn, status_file: str = STATUS_FILE) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    with conn.cursor() as cur:
        # events.policy_digest is authoritative (ATTEST envelope); the source_revision
        # regex covers pre-fix events emitted before the column was populated
        cur.execute(
            "SELECT e.recorded_at, eo.value_bool, e.signature IS NOT NULL, "
            "       COALESCE(e.policy_digest, "
            "                substring(eo.source_revision FROM 'policy_digest=(sha256:[0-9a-f]+)')) "
            "FROM event_observation eo JOIN events e ON e.event_id = eo.event_id "
            "WHERE eo.predicate='archive_restore_proof' ORDER BY e.recorded_at")
        proofs = [(r[0], bool(r[1]), bool(r[2]), r[3]) for r in cur.fetchall()]
        cur.execute(
            "SELECT e.recorded_at, eo.value_number "
            "FROM event_observation eo JOIN events e ON e.event_id = eo.event_id "
            "WHERE eo.predicate='work_surfaced'")
        surfaced = [(r[0], r[1]) for r in cur.fetchall()]
        cur.execute("SELECT execution FROM current_work WHERE work_ref=%s", (DRILL2_WORK,))
        row = cur.fetchone()
        drill2_execution = row[0] if row else None

    report = assemble(proofs, surfaced, drill2_execution,
                      read_supervisor_status(status_file, now), now)
    try:                                            # informational only, never a gap
        out = subprocess.run(
            ["systemctl", "--user", "show", "kawa-supervisor.service", "-p", "NRestarts"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        report["supervisor_nrestarts"] = int(out.split("=", 1)[1]) if "=" in out else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        report["supervisor_nrestarts"] = None
    return report


def main() -> int:
    from kawa.storage.db import connect
    try:
        report = compute(connect())
    except Exception as exc:                        # noqa: BLE001 — CLI boundary
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
    else:
        verdict = "STANDING — the Result may be recorded" if report["standing"] else "NOT YET"
        print(f"step-12 durability standing: {verdict}")
        print(f"  archive streak {report['archive_streak_days']}d · clock day "
              f"{report['days_elapsed']}/{REQUIRED_DAYS} · earliest Result {report['earliest_result_date']}")
        for g in report["gaps"]:
            print(f"  gap: {g}")
    return 0 if report["standing"] else 1


if __name__ == "__main__":
    sys.exit(main())
