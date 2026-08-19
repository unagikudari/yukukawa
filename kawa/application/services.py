"""Application services — the use cases an Agent/CLI calls.

Each mutating call is emit (durable Event) + reduce (projection update) in ONE transaction, so
the Event and its projection commit together. Reads come from the disposable projections.
This is the surface the #53 dogfood loop and the #56 compatibility adapter sit on.
"""
from __future__ import annotations

import psycopg

from kawa.domain.ids import hlc_order_sql

from kawa.domain.events import (
    ClaimRecorded,
    Event,
    LinkAsserted,
    ObservationRecorded,
    Payload,
    PlanCreated,
    PlanLifecycleChanged,
    ResultRecorded,
    WorkDependencyDeclared,
    WorkDerived,
    WorkRetired,
)
from kawa.projections.reducers import reduce
from kawa.storage.emit import Emitter
from kawa.domain.identity import IdentityContext

# #156 Phase A: managed domain tokens (fail-closed authoring validator, r3-F1).
# Unknown tokens are rejected at write time; ABSENT tokens are permitted — the
# legacy/unmapped case surfaces as a stated `domain: UNMAPPED` frontier at
# orientation time, never as silent coverage. Loaded once per process from the
# registry SoT; the registry file rides normal PR + adversarial review.
_DOMAIN_TOKENS: frozenset[str] | None = None


def _known_domains() -> frozenset[str]:
    global _DOMAIN_TOKENS
    if _DOMAIN_TOKENS is None:
        import json
        from pathlib import Path
        reg = Path(__file__).resolve().parents[2] / "registry" / "vocabulary.json"
        try:
            _DOMAIN_TOKENS = frozenset(json.loads(reg.read_text()).get("domains", {}))
        except OSError:
            _DOMAIN_TOKENS = frozenset()
    return _DOMAIN_TOKENS


def _validate_domain(domain: str | None) -> None:
    if domain is None:
        return
    known = _known_domains()
    if domain not in known:
        raise ValueError(
            f"unknown domain token {domain!r} — domain must be a managed vocabulary token "
            f"(registry/vocabulary.json 'domains'; known: {', '.join(sorted(known)) or 'none'}); "
            "free text is not a domain (#156 r3-F1)")


class Kawa:
    def __init__(self, conn: psycopg.Connection, *, identity: IdentityContext,
                 default_scope: str | None = "fleet") -> None:
        """`default_scope` (#113 9b, BC-iii): the emitter-configuration half of the atomic
        least-visible flip — every emit through this service is envelope-v2 `fleet`-scoped by
        default, matching the `fleet` grant `TrustRegistry.enroll` records by default. Pass
        `default_scope=None` for a legacy v1 emitter (the stated rolling-transition carve-out)."""
        self.conn = conn
        self.emitter = Emitter(conn, identity=identity)
        self.default_scope = default_scope

    def _emit_reduce(self, payload: Payload, **kw: str | None) -> Event:
        kw.setdefault("scope_ref", self.default_scope)   # type: ignore[arg-type]
        event = self.emitter.emit(payload, **kw)  # type: ignore[arg-type]
        with self.conn.cursor() as cur:
            reduce(cur, event)
        self.conn.commit()
        return event

    # ---- writes ----
    def create_plan(self, plan_ref: str, project_ref: str, objective: str,
                    rationale: str | None = None, scope: str | None = None,
                    constraints: list[str] | None = None,
                    expected_observations: list[str] | None = None,
                    domain: str | None = None) -> Event:
        _validate_domain(domain)
        return self._emit_reduce(
            PlanCreated(plan_ref=plan_ref, project_ref=project_ref, objective=objective,
                        rationale=rationale, scope=scope, constraints=constraints,
                        expected_observations=expected_observations, domain=domain)
        )

    def set_plan_lifecycle(self, plan_ref: str, lifecycle: str,
                           end_reason: str | None = None) -> Event:
        return self._emit_reduce(
            PlanLifecycleChanged(plan_ref=plan_ref, lifecycle=lifecycle, end_reason=end_reason)  # type: ignore[arg-type]
        )

    def derive_work(self, work_ref: str, plan_ref: str, work_kind: str,
                    role_requirement: str | None = None, subject_ref: str | None = None,
                    objective: str | None = None, constraints: list[str] | None = None,
                    expected_observations: list[str] | None = None,
                    domain: str | None = None) -> Event:
        _validate_domain(domain)
        return self._emit_reduce(
            WorkDerived(work_ref=work_ref, plan_ref=plan_ref, work_kind=work_kind,
                        role_requirement=role_requirement, subject_ref=subject_ref,
                        objective=objective, constraints=constraints,
                        expected_observations=expected_observations, domain=domain)
        )

    def retire_work(self, work_ref: str, reason: str, note: str | None = None) -> Event:
        """#93: intentional withdrawal — the third terminal. Fabricates no Result; terminal
        for this work_ref (a plan revision derives a NEW ref instead of un-retiring)."""
        return self._emit_reduce(
            WorkRetired(work_ref=work_ref, reason=reason, note=note)  # type: ignore[arg-type]
        )

    def declare_dependency(self, work_ref: str, dependency_work_ref: str,
                           satisfaction_policy: str = "ALL") -> Event:
        return self._emit_reduce(
            WorkDependencyDeclared(work_ref=work_ref, dependency_work_ref=dependency_work_ref,
                                   satisfaction_policy=satisfaction_policy)  # type: ignore[arg-type]
        )

    def record_result(self, work_ref: str, outcome: str, result_ref: str,
                      summary: str | None = None,
                      occurrence_key: str | None = None) -> Event:
        """`occurrence_key` (#111 8D): derive via `occurrence.work_occurrence_key` with the
        causal prior FROM THE RETRY TRIGGER (BC-1) — never from a local latest-Result query."""
        return self._emit_reduce(
            ResultRecorded(work_ref=work_ref, outcome=outcome, result_ref=result_ref,  # type: ignore[arg-type]
                           summary=summary, occurrence_key=occurrence_key)
        )

    def record_observation(self, predicate: str, *, value_text: str | None = None,
                           value_number: float | None = None, value_bool: bool | None = None,
                           value_time: str | None = None, method: str, occurred_at: str | None = None,
                           subject_ref: str | None = None, source_ref: str | None = None,
                           source_revision: str | None = None, content_digest: str | None = None,
                           fetched_at: str | None = None,
                           policy_digest: str | None = None) -> Event:
        # policy_digest rides the ATTEST envelope (emit §2.2: "policy in force"), so a
        # policy-bound Observation is machine-queryable via events.policy_digest instead
        # of only a prose fragment inside source_revision
        return self._emit_reduce(
            ObservationRecorded(predicate=predicate, value_text=value_text, value_number=value_number,
                                value_bool=value_bool, value_time=value_time,
                                observation_method_class=method, occurred_at=occurred_at,  # type: ignore[arg-type]
                                source_ref=source_ref, source_revision=source_revision,
                                content_digest=content_digest, fetched_at=fetched_at),
            subject_ref=subject_ref, policy_digest=policy_digest,
        )

    def record_claim(self, proposition: str, *, basis_note: str | None = None,
                     subject_ref: str | None = None) -> Event:
        return self._emit_reduce(
            ClaimRecorded(proposition=proposition, basis_note=basis_note), subject_ref=subject_ref
        )

    def assert_link(self, source_ref: str, relation: str, target_ref: str) -> Event:
        return self._emit_reduce(
            LinkAsserted(source_ref=source_ref, relation=relation, target_ref=target_ref)  # type: ignore[arg-type]
        )

    def claim_standing(self, claim_event_id: str) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT standing FROM current_claim_standing WHERE claim_event_id=%s",
                        (claim_event_id,))
            row = cur.fetchone()
        return row[0] if row else None

    # ---- reads (from projections) ----
    def work_next(self, role: str | None = None) -> dict | None:
        """The next actionable Work as a structured contract + JIT instruction (v0.5 §8).
        execution='ready', FIFO by ready_at; role_requirement is a FILTER here, never a
        readiness input. The instruction is rendered on read from typed fields only
        (kawa.application.jit) and is never persisted; instruction_basis.consumed lists
        the event ids of the projection rows the render read."""
        from kawa.application.jit import RENDERER_VERSION, RenderInput, render_instruction
        # LATERAL picks exactly ONE event_work row per work (the latest derive, by causal
        # order) — the same work_ref can carry several WorkDerived events (re-derive is
        # legal and exists in the dogfood log), and a bare JOIN would multiply rows.
        sql = ("SELECT w.work_ref, w.plan_ref, w.work_kind, w.role_requirement, "
               "ew.objective, ew.constraints, ew.expected_observations, "
               "w.latest_event_id, p.objective, p.latest_event_id "
               "FROM current_work w "
               "JOIN current_plans p ON p.plan_ref = w.plan_ref "
               "LEFT JOIN LATERAL ("
               "  SELECT e2.objective, e2.constraints, e2.expected_observations "
               "  FROM event_work e2 JOIN events ev ON ev.event_id = e2.event_id "
               "  WHERE e2.work_ref = w.work_ref "
               f"  ORDER BY {hlc_order_sql(hlc='ev.hlc', tiebreak='ev.origin_node', unique='ev.event_id')} "
               "  LIMIT 1) ew ON true "
               "WHERE w.execution='ready'")
        params: list[str] = []
        if role is not None:
            sql += " AND w.role_requirement=%s"
            params.append(role)
        sql += " ORDER BY w.ready_at NULLS LAST, w.work_ref LIMIT 1"
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        if row is None:
            return None
        (work_ref, plan_ref, work_kind, role_req, objective, cons, eobs,
         w_event, plan_obj, p_event) = row
        inp = RenderInput(work_ref=work_ref, plan_ref=plan_ref, work_kind=work_kind,
                          role_requirement=role_req, objective=objective,
                          constraints=tuple(cons) if cons else None,
                          expected_observations=tuple(eobs) if eobs else None,
                          plan_objective=plan_obj)
        return {
            "work": {"work_ref": work_ref, "plan_ref": plan_ref, "work_kind": work_kind,
                     "role_requirement": role_req, "objective": objective,
                     "constraints": list(cons) if cons else None,
                     "expected_observations": list(eobs) if eobs else None},
            "instruction": render_instruction(inp),
            "instruction_basis": {"plan_ref": plan_ref, "work_ref": work_ref,
                                  "consumed": [w_event, p_event],
                                  "renderer_version": RENDERER_VERSION},
        }

    def work_next_for_participant(self, registry, session_id: str, *, claims=None,
                                  now: float | None = None) -> dict:
        """Capability-gated Work discovery for a reachable participant (v0.5 §16.5 + §7).
        A STRICTLY NARROWER view than work_next — never a substitute for it: reachability
        volatility narrows discovery, never Work availability. Returns {work?, reason} with a
        CLOSED reason taxonomy so the caller can act on the exact 'why nothing':
          unreachable    — no live session (unregistered / dropped / restarted)
          unauthorized   — session live, but not authorized for any ready Work's role
          no_ready_work  — authorized, but nothing is currently ready (or all live-claimed)
          ok             — a Work is returned
        The role→`role:<X>` projection here is an INTERNAL ADAPTER for the authorization
        check only; role stays the Work-eligibility axis and capability the participant-
        authorization axis (they never merge). Readiness is untouched — a ready Work with no
        authorized participant stays ready and is still returned by the plain work_next.
        When a ClaimRegistry is given (§7), Work actively claimed by a DIFFERENT live session
        is a temporary DISCOVERY exclusion (not a readiness change); an expired claim is
        absent, so a ghost claim never suppresses this pull."""
        session = registry.lookup(session_id)
        if session is None:
            return {"work": None, "reason": "unreachable"}
        # roles this participant is authorized for (role:<X> capabilities only — the adapter)
        roles = {c[len("role:"):] for c in session.authorized if c.startswith("role:")}
        if not roles:
            return {"work": None, "reason": "unauthorized"}
        # is there ANY ready Work at all (to distinguish unauthorized from no_ready_work)?
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT role_requirement FROM current_work WHERE execution='ready'")
            ready_roles = {r[0] for r in cur.fetchall()}
        if not ready_roles:
            return {"work": None, "reason": "no_ready_work"}
        if not (roles & {r for r in ready_roles if r is not None}):
            return {"work": None, "reason": "unauthorized"}   # ready Work exists, none for these roles
        for role in sorted(roles):
            nxt = self._work_next_unclaimed(role=role, claims=claims, session_id=session_id, now=now)
            if nxt is not None:
                return {"work": nxt, "reason": "ok"}
        return {"work": None, "reason": "no_ready_work"}      # all matching Work is live-claimed

    def _work_next_unclaimed(self, *, role, claims, session_id, now):  # type: ignore[no-untyped-def]
        """work_next(role), skipping Work held by a DIFFERENT live session. Iterates ready
        Work in the same FIFO order until it finds one this participant may take (unclaimed,
        expired-claim, or its own claim). Claims never touch readiness — the exclusion is
        applied here at the discovery boundary only."""
        if claims is None:
            return self.work_next(role=role)
        with self.conn.cursor() as cur:
            cur.execute("SELECT work_ref FROM current_work WHERE execution='ready' AND "
                        "role_requirement=%s ORDER BY ready_at NULLS LAST, work_ref", (role,))
            candidates = [r[0] for r in cur.fetchall()]
        for work_ref in candidates:
            holder = claims.holder(work_ref, now if now is not None else 0.0)
            if holder is None or holder == session_id:        # free / expired / own claim
                return self._work_contract(work_ref)
        return None

    def _work_contract(self, work_ref: str) -> dict | None:
        """The structured Work contract + JIT instruction for a specific ready work_ref
        (shared by work_next and the claim-aware discovery path)."""
        from kawa.application.jit import RENDERER_VERSION, RenderInput, render_instruction
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT w.work_ref, w.plan_ref, w.work_kind, w.role_requirement, "
                "ew.objective, ew.constraints, ew.expected_observations, "
                "w.latest_event_id, p.objective, p.latest_event_id "
                "FROM current_work w JOIN current_plans p ON p.plan_ref = w.plan_ref "
                "LEFT JOIN LATERAL (SELECT e2.objective, e2.constraints, e2.expected_observations "
                "  FROM event_work e2 JOIN events ev ON ev.event_id = e2.event_id "
                "  WHERE e2.work_ref = w.work_ref "
                f"  ORDER BY {hlc_order_sql(hlc='ev.hlc', tiebreak='ev.origin_node', unique='ev.event_id')} LIMIT 1) ew ON true "
                "WHERE w.work_ref=%s AND w.execution='ready'", (work_ref,))
            row = cur.fetchone()
        if row is None:
            return None
        (wr, plan_ref, work_kind, role_req, objective, cons, eobs, w_event, plan_obj, p_event) = row
        inp = RenderInput(work_ref=wr, plan_ref=plan_ref, work_kind=work_kind, role_requirement=role_req,
                          objective=objective, constraints=tuple(cons) if cons else None,
                          expected_observations=tuple(eobs) if eobs else None, plan_objective=plan_obj)
        return {
            "work": {"work_ref": wr, "plan_ref": plan_ref, "work_kind": work_kind,
                     "role_requirement": role_req, "objective": objective,
                     "constraints": list(cons) if cons else None,
                     "expected_observations": list(eobs) if eobs else None},
            "instruction": render_instruction(inp),
            "instruction_basis": {"plan_ref": plan_ref, "work_ref": wr, "consumed": [w_event, p_event],
                                  "renderer_version": RENDERER_VERSION},
        }

    def plan_progress(self, plan_ref: str) -> dict[str, int]:
        """§6.3: progress is a read-time projection over the Work DAG — never a stored
        percentage. Counts by execution state (incl. 'retired' as its own bucket)."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT execution, count(*) FROM current_work WHERE plan_ref=%s "
                        "GROUP BY execution ORDER BY execution", (plan_ref,))
            return {state: n for state, n in cur.fetchall()}

    def work_state(self, work_ref: str) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT execution FROM current_work WHERE work_ref=%s", (work_ref,))
            row = cur.fetchone()
        return row[0] if row else None

    def plan_state(self, plan_ref: str) -> tuple[str, str | None] | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT lifecycle, end_reason FROM current_plans WHERE plan_ref=%s", (plan_ref,))
            row = cur.fetchone()
        return (row[0], row[1]) if row else None
