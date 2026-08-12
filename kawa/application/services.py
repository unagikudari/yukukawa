"""Application services — the use cases an Agent/CLI calls.

Each mutating call is emit (durable Event) + reduce (projection update) in ONE transaction, so
the Event and its projection commit together. Reads come from the disposable projections.
This is the surface the #53 dogfood loop and the #56 compatibility adapter sit on.
"""
from __future__ import annotations

import psycopg

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


class Kawa:
    def __init__(self, conn: psycopg.Connection, *, identity: IdentityContext) -> None:
        self.conn = conn
        self.emitter = Emitter(conn, identity=identity)

    def _emit_reduce(self, payload: Payload, **kw: str | None) -> Event:
        event = self.emitter.emit(payload, **kw)  # type: ignore[arg-type]
        with self.conn.cursor() as cur:
            reduce(cur, event)
        self.conn.commit()
        return event

    # ---- writes ----
    def create_plan(self, plan_ref: str, project_ref: str, objective: str,
                    rationale: str | None = None, scope: str | None = None,
                    constraints: list[str] | None = None,
                    expected_observations: list[str] | None = None) -> Event:
        return self._emit_reduce(
            PlanCreated(plan_ref=plan_ref, project_ref=project_ref, objective=objective,
                        rationale=rationale, scope=scope, constraints=constraints,
                        expected_observations=expected_observations)
        )

    def set_plan_lifecycle(self, plan_ref: str, lifecycle: str,
                           end_reason: str | None = None) -> Event:
        return self._emit_reduce(
            PlanLifecycleChanged(plan_ref=plan_ref, lifecycle=lifecycle, end_reason=end_reason)  # type: ignore[arg-type]
        )

    def derive_work(self, work_ref: str, plan_ref: str, work_kind: str,
                    role_requirement: str | None = None, subject_ref: str | None = None,
                    objective: str | None = None, constraints: list[str] | None = None,
                    expected_observations: list[str] | None = None) -> Event:
        return self._emit_reduce(
            WorkDerived(work_ref=work_ref, plan_ref=plan_ref, work_kind=work_kind,
                        role_requirement=role_requirement, subject_ref=subject_ref,
                        objective=objective, constraints=constraints,
                        expected_observations=expected_observations)
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
                      summary: str | None = None) -> Event:
        return self._emit_reduce(
            ResultRecorded(work_ref=work_ref, outcome=outcome, result_ref=result_ref,  # type: ignore[arg-type]
                           summary=summary)
        )

    def record_observation(self, predicate: str, *, value_text: str | None = None,
                           value_number: float | None = None, value_bool: bool | None = None,
                           value_time: str | None = None, method: str, occurred_at: str | None = None,
                           subject_ref: str | None = None, source_ref: str | None = None,
                           source_revision: str | None = None, content_digest: str | None = None,
                           fetched_at: str | None = None) -> Event:
        return self._emit_reduce(
            ObservationRecorded(predicate=predicate, value_text=value_text, value_number=value_number,
                                value_bool=value_bool, value_time=value_time,
                                observation_method_class=method, occurred_at=occurred_at,  # type: ignore[arg-type]
                                source_ref=source_ref, source_revision=source_revision,
                                content_digest=content_digest, fetched_at=fetched_at),
            subject_ref=subject_ref,
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
               "  ORDER BY split_part(ev.hlc,'.',1)::bigint DESC, "
               "           split_part(ev.hlc,'.',2)::bigint DESC, ev.origin_node DESC "
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
