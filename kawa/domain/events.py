"""Typed Event vocabulary — the schema-first Domain contract (#57 §5).

The Python types here *implement* the durable contract (sql/0001, the frozen slabs); they do
not define it. Payloads are typed per-kind models, never a `dict[str, Any]` bag (#57 §6).
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from kawa.domain.ids import digest, event_hash


class EventKind(str, Enum):
    PLAN_CREATED = "plan.created"
    PLAN_LIFECYCLE_CHANGED = "plan.lifecycle_changed"
    WORK_DERIVED = "work.derived"
    WORK_DEPENDENCY_DECLARED = "work.dependency_declared"
    RESULT_RECORDED = "result.recorded"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)  # fail-loud: no stray fields (#57 §6)


class PlanCreated(_Payload):
    kind: Literal[EventKind.PLAN_CREATED] = EventKind.PLAN_CREATED
    plan_ref: str
    project_ref: str
    objective: str
    rationale: str | None = None
    lifecycle: Literal["draft", "reviewing", "ready", "running", "blocked", "ended"] = "draft"


class PlanLifecycleChanged(_Payload):
    kind: Literal[EventKind.PLAN_LIFECYCLE_CHANGED] = EventKind.PLAN_LIFECYCLE_CHANGED
    plan_ref: str
    lifecycle: Literal["draft", "reviewing", "ready", "running", "blocked", "ended"]
    end_reason: Literal["completed", "cancelled", "failed", "superseded"] | None = None


class WorkDerived(_Payload):
    kind: Literal[EventKind.WORK_DERIVED] = EventKind.WORK_DERIVED
    work_ref: str
    plan_ref: str
    work_kind: str
    role_requirement: str | None = None
    subject_ref: str | None = None


class WorkDependencyDeclared(_Payload):
    kind: Literal[EventKind.WORK_DEPENDENCY_DECLARED] = EventKind.WORK_DEPENDENCY_DECLARED
    work_ref: str
    dependency_work_ref: str
    satisfaction_policy: Literal["ALL", "ANY"] = "ALL"


class ResultRecorded(_Payload):
    kind: Literal[EventKind.RESULT_RECORDED] = EventKind.RESULT_RECORDED
    work_ref: str
    outcome: Literal["success", "failure", "conflicted", "execution_unknown"]
    result_ref: str
    summary: str | None = None


Payload = Annotated[
    Union[PlanCreated, PlanLifecycleChanged, WorkDerived, WorkDependencyDeclared, ResultRecorded],
    Field(discriminator="kind"),
]


class Event(BaseModel):
    """A committed Event envelope. `event_id == self_hash` (content-addressed, chain-linked)."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    origin_node: str
    origin_seq: int
    hlc: str
    kind: EventKind
    subject_ref: str | None
    actor_ref: str
    policy_digest: str | None
    payload_digest: str
    prev_hash: str | None
    self_hash: str
    # provenance (Phase 4A) — over content_hash (= self_hash); NEVER part of Event identity or verify()
    signature: str | None = None
    signing_key_ref: str | None = None       # which key signed; resolves the historical pubkey (rotation-safe)
    signature_scheme: str | None = None      # mechanics/profile (e.g. 'ed25519'); not a Core Domain enum
    payload: Payload

    def verify(self) -> bool:
        """Re-derive payload_digest and self_hash; a committed Event must be self-consistent."""
        pd = digest(self.payload.model_dump(mode="json"))
        sh = event_hash(
            origin_node=self.origin_node,
            origin_seq=self.origin_seq,
            hlc=self.hlc,
            kind=self.kind.value,
            subject_ref=self.subject_ref,
            actor_ref=self.actor_ref,
            policy_digest=self.policy_digest,
            payload_digest=pd,
            prev_hash=self.prev_hash,
        )
        return pd == self.payload_digest and sh == self.self_hash == self.event_id
