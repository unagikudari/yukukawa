"""Deterministic reducers: Event → current_* projections.

Projections are disposable: `rebuild()` truncates them and replays the Event log to an
equivalent state (the read-model contract). Reducers are the ONLY writers of current_* tables.
"""
from __future__ import annotations

import json

import psycopg

from kawa.domain.events import (
    AuthorityConfiguration,
    AuthorityReceipt,
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
    WorkRetired,
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

# deterministic "latest Result" for a work: causal HLC order, origin as final tiebreak (§3).
# Quarantined duplicates (#111 BC-2) are invisible here — a duplicate occurrence never moves
# work state or dependency state, on any node, in any arrival order.
_LATEST_RESULT_SQL = (
    "SELECT er.outcome, er.result_ref FROM event_result er "
    "JOIN events e ON e.event_id = er.event_id WHERE er.work_ref = %s "
    "AND NOT EXISTS (SELECT 1 FROM result_occurrence_quarantine q WHERE q.event_id = er.event_id) "
    "ORDER BY split_part(e.hlc,'.',1)::bigint DESC, split_part(e.hlc,'.',2)::bigint DESC, "
    "e.origin_node DESC LIMIT 1"
)

# first consumer of an occurrence key: the same causal total order, ascending — arrival and
# replay order cannot change who consumed first, so quarantine membership is convergent.
_OCCURRENCE_CONSUMERS_SQL = (
    "SELECT er.event_id FROM event_result er "
    "JOIN events e ON e.event_id = er.event_id "
    "WHERE er.work_ref = %s AND er.occurrence_key = %s "
    "ORDER BY split_part(e.hlc,'.',1)::bigint, split_part(e.hlc,'.',2)::bigint, e.origin_node"
)


def _requarantine_occurrence(cur: psycopg.Cursor, work_ref: str, occurrence_key: str) -> None:
    """BC-2 duplicate containment, recomputed deterministically. The FIRST consumer in the
    causal total order keeps the key; every other Result carrying it is quarantined — recorded
    (the log is append-only; history is never refused after admission) but inert for
    projections. Recomputed wholesale so a late-arriving earlier consumer converges every
    node to the same winner regardless of arrival order."""
    cur.execute(_OCCURRENCE_CONSUMERS_SQL, (work_ref, occurrence_key))
    consumers = [r[0] for r in cur.fetchall()]
    if not consumers:
        return
    winner, losers = consumers[0], consumers[1:]
    cur.execute("DELETE FROM result_occurrence_quarantine WHERE work_ref=%s AND occurrence_key=%s",
                (work_ref, occurrence_key))
    for loser in losers:
        cur.execute(
            "INSERT INTO result_occurrence_quarantine (event_id, work_ref, occurrence_key, "
            "first_event_id) VALUES (%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING",
            (loser, work_ref, occurrence_key, winner),
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
        # #102 round-2 constraint 1: a NEW dependency on an already-retired Work resolves to
        # 'retired' immediately — past Results are irrelevant (retirement dominates), and
        # without this the edge would hang at 'pending' forever or resurrect via old Results.
        cur.execute("SELECT execution FROM current_work WHERE work_ref=%s", (p.dependency_work_ref,))
        row = cur.fetchone()
        if row is not None and row[0] == "retired":
            cur.execute(
                "UPDATE current_work_dependency SET dependency_state='retired', "
                "updated_at=clock_timestamp() WHERE work_ref=%s AND dependency_work_ref=%s",
                (p.work_ref, p.dependency_work_ref),
            )
        else:
            # #87: a dependency on a Work whose Result already exists resolves NOW, from the
            # same predicate the Result path applies — never deadlocks at 'pending'.
            _apply_latest_result_to_dependents(cur, p.dependency_work_ref, event.event_id,
                                               only_dependent=p.work_ref)
        _recompute_readiness(cur, p.work_ref, event.event_id)
    elif isinstance(p, ResultRecorded):
        # Both the work's own state and its dependents' dependency_state follow the LATEST
        # Result (pure predicate — replay-order independent even for late-arriving results).
        # Retirement dominates (#102): a Result for a retired Work stays recorded in the log
        # but is inert for projections — own-exec stays 'retired', dependents stay 'retired'.
        # Occurrence containment first (#111 BC-2): settle who consumed this key, THEN read
        # the latest non-quarantined Result — a duplicate changes nothing downstream.
        if p.occurrence_key is not None:
            _requarantine_occurrence(cur, p.work_ref, p.occurrence_key)
        cur.execute(_LATEST_RESULT_SQL, (p.work_ref,))
        outcome, result_ref = cur.fetchone()  # at least this event's own row exists
        cur.execute(
            "UPDATE current_work SET execution=%s, latest_event_id=%s, updated_at=clock_timestamp() "
            "WHERE work_ref=%s AND execution <> 'retired'",
            (_OWN_EXEC[outcome], event.event_id, p.work_ref),
        )
        _apply_latest_result_to_dependents(cur, p.work_ref, event.event_id)
        cur.execute(
            "SELECT DISTINCT work_ref FROM current_work_dependency WHERE dependency_work_ref=%s",
            (p.work_ref,),
        )
        for (dependent_ref,) in cur.fetchall():
            _recompute_readiness(cur, dependent_ref, event.event_id)
    elif isinstance(p, WorkRetired):
        # Terminal for the Work identity: overwrites whatever came before, and nothing
        # (including later Results) overwrites it back. Both event orders converge.
        cur.execute(
            "UPDATE current_work SET execution='retired', latest_event_id=%s, "
            "updated_at=clock_timestamp() WHERE work_ref=%s",
            (event.event_id, p.work_ref),
        )
        cur.execute(
            "UPDATE current_work_dependency SET dependency_state='retired', "
            "updated_at=clock_timestamp() WHERE dependency_work_ref=%s",
            (p.work_ref,),
        )
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
    elif isinstance(p, (AuthorityConfiguration, AuthorityReceipt)):
        # Authority events are PROOF material, not Domain state: admission stores them
        # (the typed payload row is the verifier's chain source); no projection moves.
        # Standing is computed by the three-state verifier at read time — a Receipt's
        # admission proves provenance, never authority (#118 10B).
        pass
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
    # retirement dominates: a 'retired' edge is never overwritten by Result-derived state
    sql = ("UPDATE current_work_dependency SET dependency_state=%s, result_ref=%s, "
           "updated_at=clock_timestamp() WHERE dependency_work_ref=%s "
           "AND dependency_state <> 'retired'")
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
    # #102: retired is a SEPARATE branch, never funneled through has_failed — "blocked by an
    # intentional withdrawal" and "blocked by a failure" must stay distinguishable downstream.
    # Both block; when both exist, retired takes display precedence (the withdrawal explains
    # the block; a retry cannot fix it) — the dependency rows keep the full distinction.
    has_retired = any(s == "retired" for _, s in rows)
    policy = rows[0][0] if rows else "ALL"

    if total == 0:
        execution = "ready"
    elif policy == "ANY":
        # ANY means any one satisfied alternative suffices — a retired (or failed) sibling
        # must not block a path that already resolved (#102 review point d).
        execution = "ready" if resolved >= 1 else "blocked"
    elif has_retired or has_failed:
        execution = "blocked"
    elif resolved == total:
        execution = "ready"
    else:
        execution = "blocked"

    cur.execute(
        "UPDATE current_work SET dependency_total=%s, dependency_satisfied=%s, "
        "dependency_conflicted=%s, execution=%s, "
        "ready_at = CASE WHEN %s THEN clock_timestamp() ELSE NULL END, "
        "latest_event_id=%s, updated_at=clock_timestamp() WHERE work_ref=%s "
        "AND execution <> 'retired'",
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
            "policy_digest, payload_digest, prev_hash, self_hash, "
            "envelope_version, scope_ref, scope_digest, materialized FROM events "
            "ORDER BY recorded_at, origin_node, origin_seq"
        )
        # recorded_at is THIS node's application order — the only coordinate under which a
        # replay reproduces what the incremental reducers actually consumed. Origin-block
        # order (the pre-fix ORDER BY) inverts cross-origin causality: a work.retired that
        # arrived after a foreign origin's work.derived replays BEFORE it, its UPDATE hits
        # zero rows, and the later derive resurrects the work as ready (found live in the
        # 2026-08-16 dogfood rebuild: retired w-b resurfaced to the supervisor; plan p2
        # regressed ended->draft). The (origin_node, origin_seq) tail is a determinism
        # tie-break. Honest limits (#167): recorded_at is clock_timestamp() — a backward
        # clock step can break per-origin monotonicity — and it is NOT durable (a store
        # rebuilt from archive/pulls regenerates it in import order). Node-local fix;
        # the durable/causal coordinate and order-tolerant reducers are #167.
        rows = cur.fetchall()
        for (eid, onode, oseq, hlc, kind, subj, actor, pol, pd, prev, sh,
             ever, sref, sdig, mat) in rows:
            # the ONE materialization predicate (#113 9a): a stub reconstructs as a stub —
            # rebuild verifies its envelope but never invents domain effects from it
            payload = _load_payload(cur, eid, kind) if mat else None
            out.append(
                Event(
                    event_id=eid, origin_node=onode, origin_seq=oseq, hlc=hlc,
                    kind=EventKind(kind), subject_ref=str(subj) if subj else None,
                    actor_ref=actor, policy_digest=pol, payload_digest=pd,
                    prev_hash=prev, self_hash=sh,
                    envelope_version=ever, scope_ref=sref, scope_digest=sdig,
                    payload=payload,
                )
            )
    return out


def _load_payload(cur: psycopg.Cursor, event_id: str, kind: str) -> Payload:
    if kind == EventKind.PLAN_CREATED.value:
        cur.execute(
            "SELECT plan_ref, project_ref, objective, rationale, lifecycle, scope, constraints, "
            "expected_observations FROM event_plan WHERE event_id=%s",
            (event_id,),
        )
        plan_ref, project_ref, objective, rationale, lifecycle, scope, cons, eobs = cur.fetchone()
        return PlanCreated(plan_ref=plan_ref, project_ref=project_ref, objective=objective,
                           rationale=rationale, lifecycle=lifecycle, scope=scope,
                           constraints=cons, expected_observations=eobs)
    if kind == EventKind.PLAN_LIFECYCLE_CHANGED.value:
        cur.execute("SELECT plan_ref, lifecycle, end_reason FROM event_plan WHERE event_id=%s", (event_id,))
        plan_ref, lifecycle, end_reason = cur.fetchone()
        return PlanLifecycleChanged(plan_ref=plan_ref, lifecycle=lifecycle, end_reason=end_reason)
    if kind == EventKind.WORK_DERIVED.value:
        cur.execute(
            "SELECT work_ref, plan_ref, work_kind, role_requirement, subject_ref, objective, "
            "constraints, expected_observations FROM event_work WHERE event_id=%s",
            (event_id,),
        )
        work_ref, plan_ref, work_kind, role, subj, obj, cons, eobs = cur.fetchone()
        return WorkDerived(work_ref=work_ref, plan_ref=plan_ref, work_kind=work_kind,
                           role_requirement=role, subject_ref=str(subj) if subj else None,
                           objective=obj, constraints=cons, expected_observations=eobs)
    if kind == EventKind.WORK_DEPENDENCY_DECLARED.value:
        cur.execute(
            "SELECT work_ref, dependency_work_ref, satisfaction_policy FROM event_work_dependency WHERE event_id=%s",
            (event_id,),
        )
        work_ref, dep_ref, policy = cur.fetchone()
        return WorkDependencyDeclared(work_ref=work_ref, dependency_work_ref=dep_ref, satisfaction_policy=policy)
    if kind == EventKind.RESULT_RECORDED.value:
        cur.execute("SELECT work_ref, outcome, result_ref, summary, occurrence_key "
                    "FROM event_result WHERE event_id=%s", (event_id,))
        work_ref, outcome, result_ref, summary, occurrence_key = cur.fetchone()
        return ResultRecorded(work_ref=work_ref, outcome=outcome, result_ref=result_ref,
                              summary=summary, occurrence_key=occurrence_key)
    if kind == EventKind.AUTHORITY_CONFIGURATION.value:
        cur.execute("SELECT authority_key, configuration_digest, authority_epoch, members, "
                    "quorum, prior_configuration_digest, succession_proof "
                    "FROM event_authority_configuration WHERE event_id=%s", (event_id,))
        akey, cdig, epoch, members, quorum, prior, proof = cur.fetchone()
        return AuthorityConfiguration(authority_key=akey, configuration_digest=cdig,
                                      authority_epoch=epoch, members=json.loads(members),
                                      quorum=quorum, prior_configuration_digest=prior,
                                      succession_proof=proof)
    if kind == EventKind.AUTHORITY_RECEIPT.value:
        cur.execute("SELECT authority_key, operation_digest, configuration_digest, "
                    "authority_epoch, prior_authority_receipt_digest, policy_digest, quorum_proof "
                    "FROM event_authority_receipt WHERE event_id=%s", (event_id,))
        akey, odig, cdig, epoch, prior, pol, proof = cur.fetchone()
        return AuthorityReceipt(authority_key=akey, operation_digest=odig,
                                configuration_digest=cdig, authority_epoch=epoch,
                                prior_authority_receipt_digest=prior, policy_digest=pol,
                                quorum_proof=proof)
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
    if kind == EventKind.WORK_RETIRED.value:
        cur.execute("SELECT work_ref, reason, note FROM event_work_retired WHERE event_id=%s", (event_id,))
        work_ref, reason, note = cur.fetchone()
        return WorkRetired(work_ref=work_ref, reason=reason, note=note)
    raise RuntimeError(f"no payload loader for kind={kind}")


def rebuild(conn: psycopg.Connection) -> int:
    """DROP-equivalent: truncate projections and replay the Event log. Returns events replayed."""
    with conn.cursor() as cur:
        # result_occurrence_quarantine is a PROJECTION (derived from event_result rows, #111
        # BC-2) so it rebuilds; security_fork_evidence is EVIDENCE, not derivable — untouched.
        cur.execute("TRUNCATE current_plans, current_work, current_work_dependency, "
                    "runtime_work_occupancy, event_links, current_claim_standing, "
                    "result_occurrence_quarantine")
    events = load_events(conn)
    for event in events:
        if not event.verify():
            raise RuntimeError(f"replay integrity: event {event.event_id} failed verify")
        if event.is_stub:
            continue        # same predicate as admission (#113 9a): stubs verify, never reduce
        with conn.cursor() as cur:
            reduce(cur, event)
    conn.commit()
    return len(events)
