"""Deterministic reducers: Event → current_* projections.

Projections are disposable: `rebuild()` truncates them and replays the Event log to an
equivalent state (the read-model contract). Reducers are the ONLY writers of current_* tables.
"""
from __future__ import annotations

import psycopg

from kawa.domain.events import (
    Event,
    EventKind,
    PlanCreated,
    PlanLifecycleChanged,
    Payload,
    ResultRecorded,
    WorkDependencyDeclared,
    WorkDerived,
)

# outcome (of a dependency's Result) → dependency_state
_DEP_STATE = {
    "success": "satisfied",
    "failure": "failed",
    "conflicted": "conflicted",
    "execution_unknown": "pending",  # unknown triggers verification, not satisfaction (#53 §10)
}
# outcome → the result-work's own execution state
_OWN_EXEC = {
    "success": "finished",
    "failure": "retryable",
    "conflicted": "result_recorded",
    "execution_unknown": "execution_unknown",
}


def reduce(cur: psycopg.Cursor, event: Event) -> None:
    p = event.payload
    if isinstance(p, PlanCreated):
        cur.execute(
            "INSERT INTO current_plans (plan_ref, project_ref, objective, rationale, lifecycle, "
            "latest_event_id, latest_recorded_at) VALUES (%s,%s,%s,%s,%s,%s, clock_timestamp()) "
            "ON CONFLICT (plan_ref) DO UPDATE SET objective=EXCLUDED.objective, "
            "rationale=EXCLUDED.rationale, lifecycle=EXCLUDED.lifecycle, "
            "latest_event_id=EXCLUDED.latest_event_id, latest_recorded_at=clock_timestamp()",
            (p.plan_ref, p.project_ref, p.objective, p.rationale, p.lifecycle, event.event_id),
        )
    elif isinstance(p, PlanLifecycleChanged):
        cur.execute(
            "UPDATE current_plans SET lifecycle=%s, end_reason=%s, latest_event_id=%s, "
            "latest_recorded_at=clock_timestamp() WHERE plan_ref=%s",
            (p.lifecycle, p.end_reason, event.event_id, p.plan_ref),
        )
    elif isinstance(p, WorkDerived):
        cur.execute(
            "INSERT INTO current_work (work_ref, plan_ref, work_kind, subject_ref, role_requirement, "
            "eligibility, execution, ready_at, latest_event_id) "
            "VALUES (%s,%s,%s,%s::uuid,%s,'eligible','ready', clock_timestamp(), %s) "
            "ON CONFLICT (work_ref) DO NOTHING",
            (p.work_ref, p.plan_ref, p.work_kind, p.subject_ref, p.role_requirement, event.event_id),
        )
    elif isinstance(p, WorkDependencyDeclared):
        cur.execute(
            "INSERT INTO current_work_dependency (work_ref, dependency_work_ref, satisfaction_policy, "
            "dependency_state) VALUES (%s,%s,%s,'pending') ON CONFLICT DO NOTHING",
            (p.work_ref, p.dependency_work_ref, p.satisfaction_policy),
        )
        _recompute_readiness(cur, p.work_ref, event.event_id)
    elif isinstance(p, ResultRecorded):
        # the result-work's own state
        cur.execute(
            "UPDATE current_work SET execution=%s, latest_event_id=%s, updated_at=clock_timestamp() "
            "WHERE work_ref=%s",
            (_OWN_EXEC[p.outcome], event.event_id, p.work_ref),
        )
        # satisfy/deny dependents that were waiting on this work
        cur.execute(
            "UPDATE current_work_dependency SET dependency_state=%s, result_ref=%s, "
            "updated_at=clock_timestamp() WHERE dependency_work_ref=%s AND dependency_state='pending'",
            (_DEP_STATE[p.outcome], p.result_ref, p.work_ref),
        )
        cur.execute(
            "SELECT DISTINCT work_ref FROM current_work_dependency WHERE dependency_work_ref=%s",
            (p.work_ref,),
        )
        for (dependent_ref,) in cur.fetchall():
            _recompute_readiness(cur, dependent_ref, event.event_id)
    else:  # pragma: no cover
        raise RuntimeError(f"no reducer for {type(p).__name__}")


def _recompute_readiness(cur: psycopg.Cursor, work_ref: str, event_id: str) -> None:
    cur.execute(
        "SELECT satisfaction_policy, dependency_state FROM current_work_dependency WHERE work_ref=%s",
        (work_ref,),
    )
    rows = cur.fetchall()
    total = len(rows)
    resolved = sum(1 for _, s in rows if s in ("satisfied", "conflicted"))
    conflicted = sum(1 for _, s in rows if s == "conflicted")
    has_failed = any(s == "failed" for _, s in rows)
    policy = rows[0][0] if rows else "ALL"

    if total == 0:
        execution = "ready"
    elif has_failed:
        execution = "blocked"
    elif policy == "ALL" and resolved == total:
        execution = "ready"
    elif policy == "ANY" and resolved >= 1:
        execution = "ready"
    else:
        execution = "blocked"

    cur.execute(
        "UPDATE current_work SET dependency_total=%s, dependency_satisfied=%s, "
        "dependency_conflicted=%s, execution=%s, "
        "ready_at = CASE WHEN %s THEN clock_timestamp() ELSE NULL END, "
        "latest_event_id=%s, updated_at=clock_timestamp() WHERE work_ref=%s",
        (total, resolved, conflicted, execution, execution == "ready", event_id, work_ref),
    )


# ---- event loading + rebuild (proves projections are disposable) ----

def load_events(conn: psycopg.Connection) -> list[Event]:
    """Reconstruct Event objects from stored rows — no Python object is needed to persist them;
    the schema holds the truth (#57 §16). Each reconstructed Event re-verifies its hash chain."""
    out: list[Event] = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_id, origin_node, origin_seq, hlc, kind, subject_ref, actor_ref, "
            "policy_digest, payload_digest, prev_hash, self_hash FROM events "
            "ORDER BY origin_node, origin_seq"
        )
        rows = cur.fetchall()
        for (eid, onode, oseq, hlc, kind, subj, actor, pol, pd, prev, sh) in rows:
            payload = _load_payload(cur, eid, kind)
            out.append(
                Event(
                    event_id=eid, origin_node=onode, origin_seq=oseq, hlc=hlc,
                    kind=EventKind(kind), subject_ref=str(subj) if subj else None,
                    actor_ref=actor, policy_digest=pol, payload_digest=pd,
                    prev_hash=prev, self_hash=sh, payload=payload,
                )
            )
    return out


def _load_payload(cur: psycopg.Cursor, event_id: str, kind: str) -> Payload:
    if kind == EventKind.PLAN_CREATED.value:
        cur.execute(
            "SELECT plan_ref, project_ref, objective, rationale, lifecycle FROM event_plan WHERE event_id=%s",
            (event_id,),
        )
        plan_ref, project_ref, objective, rationale, lifecycle = cur.fetchone()
        return PlanCreated(plan_ref=plan_ref, project_ref=project_ref, objective=objective,
                           rationale=rationale, lifecycle=lifecycle)
    if kind == EventKind.PLAN_LIFECYCLE_CHANGED.value:
        cur.execute("SELECT plan_ref, lifecycle, end_reason FROM event_plan WHERE event_id=%s", (event_id,))
        plan_ref, lifecycle, end_reason = cur.fetchone()
        return PlanLifecycleChanged(plan_ref=plan_ref, lifecycle=lifecycle, end_reason=end_reason)
    if kind == EventKind.WORK_DERIVED.value:
        cur.execute(
            "SELECT work_ref, plan_ref, work_kind, role_requirement, subject_ref FROM event_work WHERE event_id=%s",
            (event_id,),
        )
        work_ref, plan_ref, work_kind, role, subj = cur.fetchone()
        return WorkDerived(work_ref=work_ref, plan_ref=plan_ref, work_kind=work_kind,
                           role_requirement=role, subject_ref=str(subj) if subj else None)
    if kind == EventKind.WORK_DEPENDENCY_DECLARED.value:
        cur.execute(
            "SELECT work_ref, dependency_work_ref, satisfaction_policy FROM event_work_dependency WHERE event_id=%s",
            (event_id,),
        )
        work_ref, dep_ref, policy = cur.fetchone()
        return WorkDependencyDeclared(work_ref=work_ref, dependency_work_ref=dep_ref, satisfaction_policy=policy)
    if kind == EventKind.RESULT_RECORDED.value:
        cur.execute("SELECT work_ref, outcome, result_ref, summary FROM event_result WHERE event_id=%s", (event_id,))
        work_ref, outcome, result_ref, summary = cur.fetchone()
        return ResultRecorded(work_ref=work_ref, outcome=outcome, result_ref=result_ref, summary=summary)
    raise RuntimeError(f"no payload loader for kind={kind}")


def rebuild(conn: psycopg.Connection) -> int:
    """DROP-equivalent: truncate projections and replay the Event log. Returns events replayed."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE current_plans, current_work, current_work_dependency, runtime_work_occupancy")
    events = load_events(conn)
    for event in events:
        if not event.verify():
            raise RuntimeError(f"replay integrity: event {event.event_id} failed verify")
        with conn.cursor() as cur:
            reduce(cur, event)
    conn.commit()
    return len(events)
