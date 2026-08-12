"""Application services — the use cases an Agent/CLI calls.

Each mutating call is emit (durable Event) + reduce (projection update) in ONE transaction, so
the Event and its projection commit together. Reads come from the disposable projections.
This is the surface the #53 dogfood loop and the #56 compatibility adapter sit on.
"""
from __future__ import annotations

from typing import TypedDict

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
)
from kawa.projections.reducers import reduce
from kawa.storage.emit import Emitter
from kawa.domain.identity import IdentityContext


class WorkItem(TypedDict):
    work_ref: str
    plan_ref: str
    work_kind: str
    role_requirement: str | None


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
                    rationale: str | None = None) -> Event:
        return self._emit_reduce(
            PlanCreated(plan_ref=plan_ref, project_ref=project_ref, objective=objective,
                        rationale=rationale)
        )

    def set_plan_lifecycle(self, plan_ref: str, lifecycle: str,
                           end_reason: str | None = None) -> Event:
        return self._emit_reduce(
            PlanLifecycleChanged(plan_ref=plan_ref, lifecycle=lifecycle, end_reason=end_reason)  # type: ignore[arg-type]
        )

    def derive_work(self, work_ref: str, plan_ref: str, work_kind: str,
                    role_requirement: str | None = None, subject_ref: str | None = None) -> Event:
        return self._emit_reduce(
            WorkDerived(work_ref=work_ref, plan_ref=plan_ref, work_kind=work_kind,
                        role_requirement=role_requirement, subject_ref=subject_ref)
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
    def work_next(self, role: str | None = None) -> WorkItem | None:
        """The next actionable Work: execution='ready', FIFO by ready_at. Results make Work
        actionable (#53) — this never blocks on another agent, only on evidence."""
        sql = ("SELECT work_ref, plan_ref, work_kind, role_requirement FROM current_work "
               "WHERE execution='ready'")
        params: list[str] = []
        if role is not None:
            sql += " AND role_requirement=%s"
            params.append(role)
        sql += " ORDER BY ready_at NULLS LAST, work_ref LIMIT 1"
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        if row is None:
            return None
        return WorkItem(work_ref=row[0], plan_ref=row[1], work_kind=row[2], role_requirement=row[3])

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
