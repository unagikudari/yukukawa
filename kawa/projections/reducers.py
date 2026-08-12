"""Deterministic reducers: Event → current_* projections.

Projections are disposable: `rebuild()` truncates them and replays the Event log to an
equivalent state (the read-model contract). Reducers are the ONLY writers of current_* tables.
"""
from __future__ import annotations

import psycopg

from kawa.domain.events import (
    ClaimRecorded,
    Event,
    EventKind,
    LinkAsserted,
    ObservationRecorded,
    PlanCreated,
    PlanLifecycleChanged,
    Payload,
    ResultRecorded,
    WorkDependencyDeclared,
    WorkDerived,
)

# outcome (of a dependency's Result) → dependency_state. This map IS the dependency
# predicate (#87 / #97 2E): it applies to the LATEST Result of the dependency Work, in both
# directions — when a Result reduces against existing declarations AND when a declaration
# reduces against an existing Result — so replay order cannot change the projection.
_DEP_STATE = {
    "success": "satisfied",
    "failure": "failed",
    "conflicted": "conflicted",
    "execution_unknown": "pending",  # unknown defers — never satisfies (#53 §10)
}
# outcome → the result-work's own execution state
_OWN_EXEC = {
    "success": "finished",
    "failure": "retryable",
    "conflicted": "result_recorded",
    "execution_unknown": "execution_unknown",
}

# deterministic "latest Result" for a work: causal HLC order, origin as final tiebreak (§3)
_LATEST_RESULT_SQL = (
    "SELECT er.outcome, er.result_ref FROM event_result er "
    "JOIN events e ON e.event_id = er.event_id WHERE er.work_ref = %s "
    "ORDER BY split_part(e.hlc,'.',1)::bigint DESC, split_part(e.hlc,'.',2)::bigint DESC, "
    "e.origin_node DESC LIMIT 1"
)


def reduce(cur: psycopg.Cursor, event: Event) -> None:
    p = event.payload
    # Universal backfill (#97 2A): ANY arriving event may be the target of links asserted
    # before it existed (cross-origin replication order) — resolve them deterministically.
    cur.execute("UPDATE event_links SET resolved = true WHERE target_ref = %s AND NOT resolved",
                (event.event_id,))
    touched_links = cur.rowcount > 0
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
        # #87: a dependency on a Work whose Result already exists resolves NOW, from the
        # same predicate the Result path applies — never deadlocks at 'pending'.
        _apply_latest_result_to_dependents(cur, p.dependency_work_ref, event.event_id,
                                           only_dependent=p.work_ref)
        _recompute_readiness(cur, p.work_ref, event.event_id)
    elif isinstance(p, ResultRecorded):
        # Both the work's own state and its dependents' dependency_state follow the LATEST
        # Result (pure predicate — replay-order independent even for late-arriving results).
        cur.execute(_LATEST_RESULT_SQL, (p.work_ref,))
        outcome, result_ref = cur.fetchone()  # at least this event's own row exists
        cur.execute(
            "UPDATE current_work SET execution=%s, latest_event_id=%s, updated_at=clock_timestamp() "
            "WHERE work_ref=%s",
            (_OWN_EXEC[outcome], event.event_id, p.work_ref),
        )
        _apply_latest_result_to_dependents(cur, p.work_ref, event.event_id)
        cur.execute(
            "SELECT DISTINCT work_ref FROM current_work_dependency WHERE dependency_work_ref=%s",
            (p.work_ref,),
        )
        for (dependent_ref,) in cur.fetchall():
            _recompute_readiness(cur, dependent_ref, event.event_id)
    elif isinstance(p, LinkAsserted):
        # projection row; dedup key = the triple, first asserter kept for attribution
        cur.execute(
            "INSERT INTO event_links (source_ref, relation, target_ref, resolved, asserted_by_event_id) "
            "VALUES (%s,%s,%s, EXISTS(SELECT 1 FROM events WHERE event_id=%s), %s) "
            "ON CONFLICT (source_ref, relation, target_ref) DO NOTHING",
            (p.source_ref, p.relation, p.target_ref, p.target_ref, event.event_id),
        )
        touched_links = True
    elif isinstance(p, ObservationRecorded):
        touched_links = True   # a new ground truth source can ground existing claims
    elif isinstance(p, ClaimRecorded):
        cur.execute(
            "INSERT INTO current_claim_standing (claim_event_id, standing, latest_event_id) "
            "VALUES (%s,'unevaluated',%s) ON CONFLICT (claim_event_id) DO NOTHING",
            (event.event_id, event.event_id),
        )
        touched_links = True
    else:  # pragma: no cover
        raise RuntimeError(f"no reducer for {type(p).__name__}")

    if touched_links:
        _recompute_standing(cur, event.event_id)


def _apply_latest_result_to_dependents(cur: psycopg.Cursor, work_ref: str, event_id: str,
                                       only_dependent: str | None = None) -> None:
    """dependency_state := _DEP_STATE[latest Result outcome of `work_ref`] — the ONE predicate
    both the declaration path (#87) and the result path apply, so declaration-before-result
    and result-before-declaration replay to identical projections."""
    cur.execute(_LATEST_RESULT_SQL, (work_ref,))
    row = cur.fetchone()
    if row is None:
        return
    outcome, result_ref = row
    sql = ("UPDATE current_work_dependency SET dependency_state=%s, result_ref=%s, "
           "updated_at=clock_timestamp() WHERE dependency_work_ref=%s")
    params: list = [_DEP_STATE[outcome], result_ref, work_ref]
    if only_dependent is not None:
        sql += " AND work_ref=%s"
        params.append(only_dependent)
    cur.execute(sql, params)


def _recompute_standing(cur: psycopg.Cursor, latest_event_id: str) -> None:
    """Derived claim standing (#97 2C) — deterministic over the CURRENT resolved-link set,
    so replay order cannot change the outcome. Single axis, five values; protocol state,
    never Truth (v0.5 §2.6). Invariants (round-1 review + round-2 binding constraints):
      - superseded is unconditional: any resolved supersedes edge retires the target,
        regardless of the asserting source's own standing (no zombie retraction);
      - grounded support = a supports-path terminating at an Observation, traversed with a
        visited-set (cycles terminate and ground nothing) and PRUNED at superseded
        intermediate claims;
      - contradicts has no algebra: it never cancels, never flips to support; sources must
        exist locally and (if claims) be non-superseded;
      - contested = grounded AND contradicted."""
    cur.execute("SELECT event_id FROM event_claim")
    claims = {r[0] for r in cur.fetchall()}
    if not claims:
        return
    cur.execute("SELECT event_id FROM event_observation")
    observations = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT source_ref, relation, target_ref FROM event_links WHERE resolved")
    links = cur.fetchall()

    superseded = {t for s, r, t in links if r == "supersedes" and t in claims}

    supports_into: dict[str, list[str]] = {}
    for s, r, t in links:
        if r == "supports":
            supports_into.setdefault(t, []).append(s)

    def grounded(claim: str) -> bool:
        stack, seen = [claim], {claim}
        while stack:
            for src in supports_into.get(stack.pop(), ()):
                if src in observations:
                    return True
                if src in claims and src not in superseded and src not in seen:
                    seen.add(src)
                    stack.append(src)
        return False

    known = claims | observations
    contradicted = {t for s, r, t in links
                    if r == "contradicts" and t in claims and s in known
                    and not (s in claims and s in superseded)}

    for c in sorted(claims):
        if c in superseded:
            standing = "superseded"
        else:
            g, k = grounded(c), c in contradicted
            standing = ("contested" if g and k else "grounded_supported" if g
                        else "contradicted" if k else "unevaluated")
        cur.execute(
            "INSERT INTO current_claim_standing (claim_event_id, standing, latest_event_id) "
            "VALUES (%s,%s,%s) ON CONFLICT (claim_event_id) DO UPDATE SET "
            "standing=EXCLUDED.standing, latest_event_id=EXCLUDED.latest_event_id, "
            "updated_at=clock_timestamp()",
            (c, standing, latest_event_id),
        )


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
    if kind == EventKind.LINK_ASSERTED.value:
        cur.execute("SELECT source_ref, relation, target_ref FROM event_link WHERE event_id=%s", (event_id,))
        source_ref, relation, target_ref = cur.fetchone()
        return LinkAsserted(source_ref=source_ref, relation=relation, target_ref=target_ref)
    if kind == EventKind.OBSERVATION_RECORDED.value:
        cur.execute(
            "SELECT predicate, value_text, value_number, value_bool, value_time, "
            "observation_method_class, occurred_at, source_ref, source_revision, "
            "content_digest, fetched_at FROM event_observation WHERE event_id=%s", (event_id,))
        (predicate, vt, vn, vb, vtime, method, occurred_at,
         source_ref, source_revision, content_digest, fetched_at) = cur.fetchone()
        return ObservationRecorded(
            predicate=predicate, value_text=vt, value_number=vn, value_bool=vb, value_time=vtime,
            observation_method_class=method, occurred_at=occurred_at, source_ref=source_ref,
            source_revision=source_revision, content_digest=content_digest, fetched_at=fetched_at)
    if kind == EventKind.CLAIM_RECORDED.value:
        cur.execute("SELECT proposition, basis_note FROM event_claim WHERE event_id=%s", (event_id,))
        proposition, basis_note = cur.fetchone()
        return ClaimRecorded(proposition=proposition, basis_note=basis_note)
    raise RuntimeError(f"no payload loader for kind={kind}")


def rebuild(conn: psycopg.Connection) -> int:
    """DROP-equivalent: truncate projections and replay the Event log. Returns events replayed."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE current_plans, current_work, current_work_dependency, "
                    "runtime_work_occupancy, event_links, current_claim_standing")
    events = load_events(conn)
    for event in events:
        if not event.verify():
            raise RuntimeError(f"replay integrity: event {event.event_id} failed verify")
        with conn.cursor() as cur:
            reduce(cur, event)
    conn.commit()
    return len(events)
