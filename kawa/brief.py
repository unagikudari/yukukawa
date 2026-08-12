"""context.bootstrap — the brief that orients a fresh session from Kawa's LIVE state, in one call.

The seamless-handoff mechanism: a new session (or the kawa MCP on session-start, once it exists) calls
`bootstrap(conn)` and learns where the work is — current understanding, the roadmap position, and the
next actionable Work — without any carried-over context. It is a READ over the disposable projections
(never a static doc): *Actors pass through Kawa; the position is derived from Kawa, and cannot go
stale.* Reads only.
"""
from __future__ import annotations

import psycopg


def bootstrap(conn: psycopg.Connection) -> dict:
    """Return the orientation a fresh session needs, derived from live projections."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events")
        events = cur.fetchone()[0]
        cur.execute("SELECT max(latest_recorded_at) FROM current_plans")
        as_of = cur.fetchone()[0]
        cur.execute("SELECT plan_ref, objective FROM current_plans WHERE lifecycle='running' ORDER BY plan_ref")
        open_plans = [{"plan_ref": r[0], "objective": r[1]} for r in cur.fetchall()]
        # the roadmap phase map, if present (plan-roadmap is the convention for the top-level plan)
        cur.execute("SELECT work_ref, execution FROM current_work WHERE plan_ref='plan-roadmap' ORDER BY work_ref")
        roadmap = [{"work_ref": r[0], "execution": r[1]} for r in cur.fetchall()]
        # next actionable Work: everything 'ready', in the same order work.next hands it out
        cur.execute("SELECT plan_ref, work_ref, work_kind, role_requirement FROM current_work "
                    "WHERE execution='ready' ORDER BY ready_at NULLS LAST, work_ref")
        next_work = [{"plan_ref": r[0], "work_ref": r[1], "work_kind": r[2], "role": r[3]}
                     for r in cur.fetchall()]
    return {
        "as_of": str(as_of)[:19] if as_of else None,
        "events": events,
        "open_plans": open_plans,
        "roadmap": roadmap,
        "next_work": next_work,
    }


def render(b: dict) -> str:
    """A readable orientation from `bootstrap()` — for a human, a CLI, or an MCP text response."""
    lines = [f"Kawa — context bootstrap  ({b['events']} events, as-of {b['as_of'] or '—'})", ""]
    lines.append("Where we are (open plans):")
    lines += [f"  {p['plan_ref']} — {p['objective']}" for p in b["open_plans"]] or ["  (none running)"]
    if b["roadmap"]:
        lines += ["", "Roadmap position:"]
        lines += [f"  {w['execution']:9s} {w['work_ref']}" for w in b["roadmap"]]
    lines += ["", "Next actionable Work (pull one, do it, record a Result to advance):"]
    if b["next_work"]:
        lines += [f"  [{w['role'] or '-'}] {w['work_ref']}  ({w['plan_ref']} · {w['work_kind']})"
                  for w in b["next_work"]]
    else:
        lines.append("  (nothing ready — all Work is blocked on evidence or finished)")
    lines += ["", "Actors pass through Kawa. Pull context from the projections; record Results to advance."]
    return "\n".join(lines)
