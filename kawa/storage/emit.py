"""The single durable write path (emit-enforcement contract).

`emit()` is the ONLY way a Domain Event becomes durable. It discharges the five obligations:
  IDENTIFY  — subject_ref (caller-supplied immutable UUIDv7) + content-addressed event_id
  ATTEST    — actor_ref (accountable emitter) + policy_digest (policy in force)
  SERIALIZE — per-origin advisory lock → gap-free origin_seq + linear hash chain
  VERIFY    — Event.verify() re-derives digest + self_hash before append
  APPEND    — insert the envelope + the typed per-kind payload row (one transaction)

Append-only (no UPDATE/DELETE) is enforced in the database (sql/0003).
"""
from __future__ import annotations

import psycopg

from kawa.domain.events import (
    Event,
    Payload,
    PlanCreated,
    PlanLifecycleChanged,
    ResultRecorded,
    WorkDependencyDeclared,
    WorkDerived,
)
from kawa.domain.ids import HLC, digest, event_hash
from kawa.domain.identity import IdentityContext


class Emitter:
    def __init__(self, conn: psycopg.Connection, *, identity: IdentityContext) -> None:
        self.conn = conn
        self.identity = identity
        self.origin_node = identity.node_ref
        self.actor_ref = identity.actor_ref
        self.hlc = HLC(node=identity.node_ref)

    def emit(
        self,
        payload: Payload,
        *,
        subject_ref: str | None = None,
        policy_digest: str | None = None,
    ) -> Event:
        with self.conn.cursor() as cur:
            # SERIALIZE: one appender per origin at a time (txn-scoped lock).
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (self.origin_node,))
            cur.execute(
                "SELECT origin_seq, self_hash FROM events "
                "WHERE origin_node = %s ORDER BY origin_seq DESC LIMIT 1",
                (self.origin_node,),
            )
            row = cur.fetchone()
            origin_seq = (row[0] + 1) if row else 1
            prev_hash = row[1] if row else None

            hlc = self.hlc.tick()
            payload_digest = digest(payload.model_dump(mode="json"))
            self_hash = event_hash(
                origin_node=self.origin_node,
                origin_seq=origin_seq,
                hlc=hlc,
                kind=payload.kind.value,
                subject_ref=subject_ref,
                actor_ref=self.actor_ref,
                policy_digest=policy_digest,
                payload_digest=payload_digest,
                prev_hash=prev_hash,
            )
            # attest provenance: sign the canonical content_hash (= self_hash). Signature is NOT
            # identity (event_id stays = content_hash) and is NOT fed back into the hash. Absent when
            # the identity is unattested (no credential) — recorded honestly as NULL, never faked.
            cred = self.identity.credential
            signature = cred.sign(self_hash) if cred is not None else None
            signing_key_ref = cred.signing_key_ref if cred is not None else None
            signature_scheme = cred.signature_scheme if cred is not None else None
            event = Event(
                event_id=self_hash,
                origin_node=self.origin_node,
                origin_seq=origin_seq,
                hlc=hlc,
                kind=payload.kind,
                subject_ref=subject_ref,
                actor_ref=self.actor_ref,
                policy_digest=policy_digest,
                payload_digest=payload_digest,
                prev_hash=prev_hash,
                self_hash=self_hash,
                signature=signature,
                signing_key_ref=signing_key_ref,
                signature_scheme=signature_scheme,
                payload=payload,
            )
            # VERIFY before APPEND — never persist an inconsistent envelope.
            if not event.verify():
                raise RuntimeError("emit VERIFY failed: envelope/hash mismatch")

            cur.execute(
                "INSERT INTO events (event_id, origin_node, origin_seq, hlc, kind, subject_ref, "
                "actor_ref, policy_digest, payload_digest, prev_hash, self_hash, "
                "signature, signing_key_ref, signature_scheme) "
                "VALUES (%s,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    event.event_id, event.origin_node, event.origin_seq, event.hlc,
                    event.kind.value, event.subject_ref, event.actor_ref, event.policy_digest,
                    event.payload_digest, event.prev_hash, event.self_hash,
                    event.signature, event.signing_key_ref, event.signature_scheme,
                ),
            )
            _insert_payload(cur, event)
        return event


def _insert_payload(cur: psycopg.Cursor, event: Event) -> None:
    p = event.payload
    eid = event.event_id
    if isinstance(p, PlanCreated):
        cur.execute(
            "INSERT INTO event_plan (event_id, plan_ref, project_ref, objective, rationale, lifecycle) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (eid, p.plan_ref, p.project_ref, p.objective, p.rationale, p.lifecycle),
        )
    elif isinstance(p, PlanLifecycleChanged):
        cur.execute(
            "INSERT INTO event_plan (event_id, plan_ref, project_ref, lifecycle, end_reason) "
            "VALUES (%s,%s,%s,%s,%s)",
            (eid, p.plan_ref, "", p.lifecycle, p.end_reason),
        )
    elif isinstance(p, WorkDerived):
        cur.execute(
            "INSERT INTO event_work (event_id, work_ref, plan_ref, work_kind, role_requirement, subject_ref) "
            "VALUES (%s,%s,%s,%s,%s,%s::uuid)",
            (eid, p.work_ref, p.plan_ref, p.work_kind, p.role_requirement, p.subject_ref),
        )
    elif isinstance(p, WorkDependencyDeclared):
        cur.execute(
            "INSERT INTO event_work_dependency (event_id, work_ref, dependency_work_ref, satisfaction_policy) "
            "VALUES (%s,%s,%s,%s)",
            (eid, p.work_ref, p.dependency_work_ref, p.satisfaction_policy),
        )
    elif isinstance(p, ResultRecorded):
        cur.execute(
            "INSERT INTO event_result (event_id, work_ref, outcome, result_ref, summary) "
            "VALUES (%s,%s,%s,%s,%s)",
            (eid, p.work_ref, p.outcome, p.result_ref, p.summary),
        )
    else:  # pragma: no cover - exhaustive over Payload union
        raise RuntimeError(f"no payload writer for {type(p).__name__}")
