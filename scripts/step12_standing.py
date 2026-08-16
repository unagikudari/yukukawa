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


def archive_streak(rows: list[tuple[dt.date, bool, bool, str | None]]) -> tuple[int, list[str]]:
    """Consecutive OK days ending at the LATEST proof day. rows = (local_date, ok, signed,
    policy_digest) ascending. A missed day, a false proof, an unsigned proof, or a policy
    digest differing from the latest run's breaks the streak (a digest change splits the
    measurement lineage by design — the streak restarts under the new policy)."""
    gaps: list[str] = []
    if not rows:
        return 0, ["no archive_restore_proof observations in the log"]
    by_day: dict[dt.date, tuple[bool, bool, str | None]] = {}
    for day, ok, signed, digest in rows:            # latest run of a day wins
        by_day[day] = (ok, signed, digest)
    last_day = max(by_day)
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
        if digest != current_digest:
            gaps.append(f"{day}: policy digest changed ({digest} != {current_digest}) — lineage split")
            break
        streak += 1
        day -= dt.timedelta(days=1)
    return streak, gaps


def compute(conn, status_file: str = STATUS_FILE) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT (e.recorded_at)::date, eo.value_bool, e.signature IS NOT NULL, "
            "       substring(eo.source_revision FROM 'policy_digest=(sha256:[0-9a-f]+)') "
            "FROM event_observation eo JOIN events e ON e.event_id = eo.event_id "
            "WHERE eo.predicate='archive_restore_proof' ORDER BY e.recorded_at")
        proof_rows = [(r[0], bool(r[1]), bool(r[2]), r[3]) for r in cur.fetchall()]
        # R1 clock start = the supervisor's first surfacing (the loop began operating)
        cur.execute(
            "SELECT min(e.recorded_at), "
            "       bool_or(eo.value_number <= %s) FILTER (WHERE eo.value_number IS NOT NULL) "
            "FROM event_observation eo JOIN events e ON e.event_id = eo.event_id "
            "WHERE eo.predicate='work_surfaced'", (PROPAGATION_BOUND_S,))
        clock_start, propagation_ok = cur.fetchone()
        cur.execute("SELECT execution FROM current_work WHERE work_ref=%s", (DRILL2_WORK,))
        row = cur.fetchone()
        drill2_execution = row[0] if row else None

    gaps: list[str] = []
    now = dt.datetime.now(dt.timezone.utc)

    streak, streak_gaps = archive_streak(proof_rows)
    gaps += streak_gaps
    if streak < REQUIRED_DAYS:
        gaps.append(f"archive restore-proof streak {streak}/{REQUIRED_DAYS} days")

    if clock_start is None:
        days_elapsed = 0
        gaps.append("no work_surfaced observation — the R1 clock has not started")
        earliest = None
    else:
        days_elapsed = (now - clock_start).days
        earliest = (clock_start + dt.timedelta(days=REQUIRED_DAYS)).date()
        if days_elapsed < REQUIRED_DAYS:
            gaps.append(f"R1 clock {days_elapsed}/{REQUIRED_DAYS} days (earliest Result {earliest})")

    if not propagation_ok:
        gaps.append(f"no work_surfaced measurement within the {PROPAGATION_BOUND_S:.0f}s bound (12C acceptance)")

    supervisor_fresh = False
    try:
        with open(status_file, encoding="utf-8") as f:
            st = json.load(f)
        age = (now - dt.datetime.strptime(st["ts"], "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=dt.timezone.utc)).total_seconds()
        supervisor_fresh = bool(st.get("ok")) and age <= SUPERVISOR_STALE_S
        if not supervisor_fresh:
            gaps.append(f"supervisor status stale/not-ok (age {int(age)}s, ok={st.get('ok')})")
    except (OSError, ValueError, KeyError) as exc:
        gaps.append(f"supervisor status unreadable: {exc}")

    if drill2_execution != "finished":
        gaps.append(f"{DRILL2_WORK} execution={drill2_execution} — replica-side evidence "
                    "(frontier-lag gap + catch-up + F5 numbers) arrives through its Result")

    nrestarts = None
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", "kawa-supervisor.service", "-p", "NRestarts"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        nrestarts = int(out.split("=", 1)[1]) if "=" in out else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass                                        # informational only, never a gap

    return {
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "standing": not gaps,
        "archive_streak_days": streak,
        "clock_start": str(clock_start)[:19] if clock_start else None,
        "days_elapsed": days_elapsed,
        "earliest_result_date": str(earliest) if earliest else None,
        "supervisor_fresh": supervisor_fresh,
        "supervisor_nrestarts": nrestarts,          # informational: restarts are allowed
        "drill2_execution": drill2_execution,
        "gaps": gaps,
    }


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
