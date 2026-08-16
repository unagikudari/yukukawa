"""context.bootstrap — the brief that orients a fresh session from Kawa's LIVE state, in one call.

The seamless-handoff mechanism: a new session (or the kawa MCP on session-start, once it exists) calls
`bootstrap(conn)` and receives a bounded ORIENTATION BUNDLE — open Plans, the roadmap position, the
next actionable Work, and anything stalled — without any carried-over context. It is a READ over the
disposable projections (never a static doc), and it is orientation records, not Kawa-authored
Situational Awareness or "the current truth" (spec-v0.5 §2 / #95): the observer constructs
understanding from it. Reads only.

Freshness contract: `as_of` is the recorded_at of the LATEST EVENT (any kind), not the latest plan
mutation — a brief that timestamps only plan changes reads as stale while work-level activity is
in full flight (observed 2026-08-16: brief said "as-of −20h" while events were minutes old).
"""
from __future__ import annotations

import psycopg

# execution states that mean "nothing to orient on": the work is done or withdrawn.
_SETTLED = ("finished", "retired")
# stalled = an explicit whitelist (review 1d352c3c F1), never NOT IN: a future ACTIVE state
# must not silently render as "stalled" and send the agent chasing phantom blockers.
# Mirrors reducers._OWN_EXEC ∪ {'blocked'}; test_e2e asserts the vocabularies stay covered.
STALLED_STATES = ("blocked", "retryable", "result_recorded", "execution_unknown")
# the canonical "latest event" order — causal HLC, origin as final tiebreak (§3, same rule
# as reducers._LATEST_RESULT_SQL). recorded_at is local wall-clock + replica arrival time,
# so it may disagree across nodes; it is DISPLAYED as the freshness stamp, never sorted on.
_LATEST_EVENT_ORDER = ("ORDER BY split_part(hlc,'.',1)::bigint DESC, "
                       "split_part(hlc,'.',2)::bigint DESC, origin_node DESC LIMIT 1")
OBJECTIVE_CLIP = 110


def _clip(text: str, limit: int = OBJECTIVE_CLIP) -> str:
    """Clip on a whitespace boundary — a blind character cut turns '#122' into '#12' and
    feeds a phantom ref to the reading agent (review 1d352c3c F4)."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    head, sep, _ = cut.rpartition(" ")
    return (head if sep and head else cut).rstrip() + "…"


def bootstrap(conn: psycopg.Connection) -> dict:
    """Return the orientation a fresh session needs, derived from live projections."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM events")
        events = cur.fetchone()[0]
        cur.execute("SELECT kind, actor_ref, recorded_at FROM events " + _LATEST_EVENT_ORDER)
        row = cur.fetchone()
        last_event = ({"kind": row[0], "actor": row[1], "at": str(row[2])[:19]}
                      if row else None)
        cur.execute("SELECT plan_ref, objective FROM current_plans WHERE lifecycle='running' ORDER BY plan_ref")
        open_plans = [{"plan_ref": r[0], "objective": r[1]} for r in cur.fetchall()]
        # §6.3 plan progress: a read-time aggregate, never a stored percentage
        for p in open_plans:
            cur.execute("SELECT execution, count(*) FROM current_work WHERE plan_ref=%s "
                        "GROUP BY execution ORDER BY execution", (p["plan_ref"],))
            p["progress"] = {state: n for state, n in cur.fetchall()}
            # ambiguous-state flag (operator request 2026-08-16, the plan-node-trust case):
            # a running plan whose every Work is settled has NOTHING in flight — it sat
            # unnoticed until a human audited it. The brief now names the limbo instead of
            # leaving "[5 finished]" to read as healthy progress.
            p["all_settled"] = bool(p["progress"]) and set(p["progress"]) <= set(_SETTLED)
        # the roadmap phase map, if present (plan-roadmap is the convention for the top-level plan)
        cur.execute("SELECT work_ref, execution FROM current_work WHERE plan_ref='plan-roadmap' ORDER BY work_ref")
        roadmap = [{"work_ref": r[0], "execution": r[1]} for r in cur.fetchall()]
        # next actionable Work: everything 'ready', in the same order work.next hands it out —
        # annotated with any live broker dispatch so two sessions don't both pull the same Work
        cur.execute(
            "SELECT w.plan_ref, w.work_ref, w.work_kind, w.role_requirement, "
            "       d.dispatch_state, d.target_agent "
            "FROM current_work w "
            "LEFT JOIN work_dispatch d ON d.work_ref = w.work_ref "
            "                         AND d.dispatch_state IN ('pending','dispatched') "
            "WHERE w.execution='ready' ORDER BY w.ready_at NULLS LAST, w.work_ref")
        next_work = [{"plan_ref": r[0], "work_ref": r[1], "work_kind": r[2], "role": r[3],
                      "dispatch": ({"state": r[4], "target": r[5]} if r[4] else None)}
                     for r in cur.fetchall()]
        # stalled Work: the explicit STALLED_STATES whitelist. This is where "nothing is
        # ready" gets its WHY — a brief that falls silent exactly when evidence is missing
        # points nowhere.
        cur.execute(
            "SELECT plan_ref, work_ref, work_kind, execution, "
            "       dependency_satisfied, dependency_total "
            "FROM current_work WHERE execution = ANY(%s) "
            "ORDER BY work_ref", (list(STALLED_STATES),))
        stalled = [{"plan_ref": r[0], "work_ref": r[1], "work_kind": r[2], "execution": r[3],
                    "dep_satisfied": r[4], "dep_total": r[5]} for r in cur.fetchall()]
        for w in stalled:
            cur.execute("SELECT dependency_work_ref, dependency_state "
                        "FROM current_work_dependency "
                        "WHERE work_ref=%s AND dependency_state <> 'satisfied' "
                        "ORDER BY dependency_work_ref", (w["work_ref"],))
            w["waiting_on"] = [{"work_ref": r[0], "state": r[1]} for r in cur.fetchall()]
    return {
        "as_of": last_event["at"] if last_event else None,
        "events": events,
        "last_event": last_event,
        "open_plans": open_plans,
        "roadmap": roadmap,
        "next_work": next_work,
        "stalled": stalled,
    }


def render(b: dict) -> str:
    """A readable orientation from `bootstrap()` — for a human, a CLI, or an MCP text response."""
    head = f"Kawa — context bootstrap  ({b['events']} events"
    if b.get("last_event"):
        le = b["last_event"]
        head += f", last activity {le['at']} · {le['kind']} by {le['actor']}"
    lines = [head + ")", ""]
    lines.append("Where we are (open plans):")
    for p in b["open_plans"]:
        prog = p.get("progress") or {}
        summary = ", ".join(f"{n} {state}" for state, n in sorted(prog.items())) or "no Work derived yet"
        if p.get("all_settled"):
            summary += " — nothing in flight: complete the plan or derive the next Work"
        lines.append(f"  {p['plan_ref']} — {_clip(p['objective'])}  [{summary}]")
    if not b["open_plans"]:
        lines.append("  (none running)")
    if b["roadmap"]:
        # settled steps collapse to counts — the un-settled lines ARE the frontier; a 20-line
        # wall of 'finished' buries the two lines that carry information
        counts = {}
        for w in b["roadmap"]:
            counts[w["execution"]] = counts.get(w["execution"], 0) + 1
        summary = " · ".join(f"{counts[s]} {s}" for s in sorted(counts))
        lines += ["", f"Roadmap position ({summary}):"]
        frontier = [w for w in b["roadmap"] if w["execution"] not in _SETTLED]
        lines += [f"  {w['execution']:9s} {w['work_ref']}" for w in frontier]
        if not frontier:
            lines.append("  (every step settled)")
    lines += ["", "Next actionable Work (pull one, do it, record a Result to advance):"]
    if b["next_work"]:
        for w in b["next_work"]:
            line = f"  [{w['role'] or '-'}] {w['work_ref']}  ({w['plan_ref']} · {w['work_kind']})"
            if w.get("dispatch"):
                line += f"  — {w['dispatch']['state']} → {w['dispatch']['target']}"
            lines.append(line)
    else:
        lines.append("  (nothing ready — see stalled Work below for what evidence is missing)"
                     if b.get("stalled") else
                     "  (nothing ready — all Work is finished or retired)")
    if b.get("stalled"):
        lines += ["", "Stalled Work (not ready, not settled — the evidence gap):"]
        for w in b["stalled"]:
            line = (f"  {w['execution']:9s} {w['work_ref']}  ({w['plan_ref']} · {w['work_kind']}, "
                    f"deps {w['dep_satisfied']}/{w['dep_total']})")
            if w["waiting_on"]:
                # a retired dependency is a DEAD branch (#102: intentional withdrawal, not a
                # failure) — mark it so the reader replans instead of waiting for evidence
                line += "  waiting on: " + ", ".join(
                    f"{d['work_ref']} (retired: dead branch — replan)" if d["state"] == "retired"
                    else f"{d['work_ref']} ({d['state']})" for d in w["waiting_on"])
            lines.append(line)
    lines += ["", "Actors pass through Kawa. Pull context from the projections; record Results to advance."]
    return "\n".join(lines)
