"""The single durable write path (emit-enforcement contract).

`emit()` is the ONLY way a Domain Event becomes durable. It discharges the five obligations:
  IDENTIFY  — subject_ref (caller-supplied immutable UUIDv7) + content-addressed event_id
  ATTEST    — actor_ref (accountable emitter) + policy_digest (policy in force)
  SERIALIZE — per-origin advisory lock → gap-free origin_seq + linear hash chain
  VERIFY    — Event.verify() re-derives digest + self_hash before append
  APPEND    — insert the envelope + the typed per-kind payload row (one transaction)

Append-only (no UPDATE/DELETE) is enforced in the database (sql/0003).

ATTEST is fail-closed against a LIVE target (#129 12B deviation review, finding 1):
a process that has named a real database (`KAWA_DSN` set — the same door `connect()`
fail-closes on) may not emit unattested events. The 2026-08-15 measurement showed why
convention is not enough: production emitted 230/230 events unsigned while the
credential sat on disk, and replication admission rejected the entire log. The test
fence (`conftest` drops `KAWA_DSN`) composes: tests emit unattested freely; naming a
live target means signing at birth.
"""
from __future__ import annotations

import os

import psycopg

from kawa.domain.events import (
    AuthorityConfiguration,
    AuthorityReceipt,
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
from kawa.domain.ids import HLC, canonical_json, digest, event_hash, scope_digest_of
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
        scope_ref: str | None = None,
        envelope_version: int | None = None,
    ) -> Event:
        """`scope_ref` (#113 9a/9b): passing it stamps an envelope-v2 event whose hash commits
        to `scope_digest_of(scope_ref)`. `envelope_version=2` with `scope_ref=None` mints an
        UNSCOPED v2 event — node-local materialization, envelope-only replication (r2 BC-v:
        no grant can ever apply; the payload is reachable only at its origin). Both omitted →
        v1, the legacy fleet-visible carve-out (byte-identical to pre-step-9)."""
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
            if envelope_version is None:
                envelope_version = 2 if scope_ref is not None else 1
            if envelope_version == 1 and scope_ref is not None:
                raise ValueError("a v1 envelope never carries a scope")
            scope_digest = scope_digest_of(scope_ref) if scope_ref is not None else None
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
                envelope_version=envelope_version,
                scope_digest=scope_digest,
            )
            # attest provenance: sign the canonical content_hash (= self_hash). Signature is NOT
            # identity (event_id stays = content_hash) and is NOT fed back into the hash. Absent when
            # the identity is unattested (no credential) — recorded honestly as NULL, never faked.
            cred = self.identity.credential
            if cred is None and os.environ.get("KAWA_DSN"):
                raise RuntimeError(
                    "unattested emit against a live target: KAWA_DSN is set but the "
                    "IdentityContext carries no credential. Construct the identity with "
                    "IdentityContext.from_local_node(load_or_create_local_node(...)) — "
                    "cross-node admission rejects unsigned events (#129 12B).")
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
                envelope_version=envelope_version,
                scope_ref=scope_ref,
                scope_digest=scope_digest,
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
                "envelope_version, scope_ref, scope_digest, "
                "signature, signing_key_ref, signature_scheme) "
                "VALUES (%s,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    event.event_id, event.origin_node, event.origin_seq, event.hlc,
                    event.kind.value, event.subject_ref, event.actor_ref, event.policy_digest,
                    event.payload_digest, event.prev_hash, event.self_hash,
                    event.envelope_version, event.scope_ref, event.scope_digest,
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
            "INSERT INTO event_plan (event_id, plan_ref, project_ref, objective, rationale, lifecycle, "
            "scope, constraints, expected_observations, domain) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (eid, p.plan_ref, p.project_ref, p.objective, p.rationale, p.lifecycle,
             p.scope, p.constraints, p.expected_observations, p.domain),
        )
    elif isinstance(p, PlanLifecycleChanged):
        cur.execute(
            "INSERT INTO event_plan (event_id, plan_ref, project_ref, lifecycle, end_reason) "
            "VALUES (%s,%s,%s,%s,%s)",
            (eid, p.plan_ref, "", p.lifecycle, p.end_reason),
        )
    elif isinstance(p, WorkDerived):
        cur.execute(
            "INSERT INTO event_work (event_id, work_ref, plan_ref, work_kind, role_requirement, subject_ref, "
            "objective, constraints, expected_observations, domain) "
            "VALUES (%s,%s,%s,%s,%s,%s::uuid,%s,%s,%s,%s)",
            (eid, p.work_ref, p.plan_ref, p.work_kind, p.role_requirement, p.subject_ref,
             p.objective, p.constraints, p.expected_observations, p.domain),
        )
    elif isinstance(p, WorkRetired):
        cur.execute(
            "INSERT INTO event_work_retired (event_id, work_ref, reason, note) VALUES (%s,%s,%s,%s)",
            (eid, p.work_ref, p.reason, p.note),
        )
    elif isinstance(p, WorkDependencyDeclared):
        cur.execute(
            "INSERT INTO event_work_dependency (event_id, work_ref, dependency_work_ref, satisfaction_policy) "
            "VALUES (%s,%s,%s,%s)",
            (eid, p.work_ref, p.dependency_work_ref, p.satisfaction_policy),
        )
    elif isinstance(p, ResultRecorded):
        cur.execute(
            "INSERT INTO event_result (event_id, work_ref, outcome, result_ref, summary, "
            "occurrence_key) VALUES (%s,%s,%s,%s,%s,%s)",
            (eid, p.work_ref, p.outcome, p.result_ref, p.summary, p.occurrence_key),
        )
    elif isinstance(p, LinkAsserted):
        cur.execute(
            "INSERT INTO event_link (event_id, source_ref, relation, target_ref) "
            "VALUES (%s,%s,%s,%s)",
            (eid, p.source_ref, p.relation, p.target_ref),
        )
    elif isinstance(p, ObservationRecorded):
        cur.execute(
            "INSERT INTO event_observation (event_id, predicate, value_text, value_number, "
            "value_bool, value_time, observation_method_class, occurred_at, "
            "source_ref, source_revision, content_digest, fetched_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (eid, p.predicate, p.value_text, p.value_number, p.value_bool, p.value_time,
             p.observation_method_class, p.occurred_at,
             p.source_ref, p.source_revision, p.content_digest, p.fetched_at),
        )
    elif isinstance(p, ClaimRecorded):
        cur.execute(
            "INSERT INTO event_claim (event_id, proposition, basis_note) VALUES (%s,%s,%s)",
            (eid, p.proposition, p.basis_note),
        )
    elif isinstance(p, AuthorityConfiguration):
        cur.execute(
            "INSERT INTO event_authority_configuration (event_id, authority_key, "
            "configuration_digest, authority_epoch, members, quorum, "
            "prior_configuration_digest, succession_proof) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (eid, p.authority_key, p.configuration_digest, p.authority_epoch,
             canonical_json(p.members), p.quorum, p.prior_configuration_digest,
             p.succession_proof),
        )
    elif isinstance(p, AuthorityReceipt):
        cur.execute(
            "INSERT INTO event_authority_receipt (event_id, authority_key, operation_digest, "
            "configuration_digest, authority_epoch, prior_authority_receipt_digest, "
            "policy_digest, quorum_proof) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (eid, p.authority_key, p.operation_digest, p.configuration_digest,
             p.authority_epoch, p.prior_authority_receipt_digest, p.policy_digest,
             p.quorum_proof),
        )
    else:  # pragma: no cover - exhaustive over Payload union
        raise RuntimeError(f"no payload writer for {type(p).__name__}")
