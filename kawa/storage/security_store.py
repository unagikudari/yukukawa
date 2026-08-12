"""Durable security-plane storage (issue #104 rev 2 §4). OUTSIDE the Domain event log and
NOT reduced — rebuild() never touches these tables (asserted). Issuance audit and
attestations are kept for LATER verification; revocation is a local forward-only deny-list.
The nonce replay cache is deliberately NOT here (it is ephemeral, per process)."""
from __future__ import annotations

import psycopg

from kawa.domain.ids import canonical_json
from kawa.domain.workload import WorkAttestation, WorkloadCredential


def record_issuance(conn: psycopg.Connection, cred: WorkloadCredential) -> None:
    p = cred.payload
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_credential_issued (jti, sub, node, runtime, workload, cnf_jkt, "
            "iss, iat, exp, capability_ctx) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (jti) DO NOTHING",
            (p["jti"], p["sub"], p.get("node"), p.get("runtime"), p.get("workload"), p["cnf_jkt"],
             p["iss"], p["iat"], p["exp"], canonical_json(p.get("capability_ctx", {}))),
        )
    conn.commit()


def record_attestation(conn: psycopg.Connection, att: WorkAttestation) -> None:
    p = att.payload
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_attestation (jti, work_ref, derived_event_id, "
            "work_semantics_digest, source_basis, policy_digest, iss, iat, exp, signature) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (jti) DO NOTHING",
            (p["jti"], p["work_ref"], p["derived_event_id"], p["work_semantics_digest"],
             canonical_json(p.get("source_basis", [])), p.get("policy_digest"),
             p["iss"], p["iat"], p["exp"], att.signature),
        )
    conn.commit()


def revoke(conn: psycopg.Connection, revoked_ref: str, kind: str, reason: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_revocation (revoked_ref, kind, reason) VALUES (%s,%s,%s) "
            "ON CONFLICT (revoked_ref) DO NOTHING", (revoked_ref, kind, reason))
    conn.commit()


def revoked_set(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT revoked_ref FROM security_revocation")
        return {r[0] for r in cur.fetchall()}


def verify_attestation_against_store(conn: psycopg.Connection, att, *, issuer_pub_pem: str,
                                     expected_issuer: str, now: str,
                                     revoked: set[str] | None = None) -> bool:
    """The TOCTOU-closing wiring (round-2 point c): recompute work_semantics_digest by
    fetching the ATTESTED immutable event by id (event_work JOIN events), and collect the
    content_digests still resolvable locally (event_observation), then verify. A Work edited
    after signing re-derives to a new event id → the attested one still yields the original
    digest, so an attacker cannot swap semantics under the same attestation; and a source
    Observation that vanished fails basis resolvability."""
    from kawa.domain.workload import verify_attestation, work_semantics_digest
    derived_event_id = att.payload.get("derived_event_id")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT work_ref, plan_ref, work_kind, role_requirement, objective, constraints, "
            "expected_observations FROM event_work WHERE event_id=%s", (derived_event_id,))
        row = cur.fetchone()
        if row is None:
            return False                                       # attested event no longer held
        recomputed = work_semantics_digest(
            work_ref=row[0], plan_ref=row[1], work_kind=row[2], role_requirement=row[3],
            objective=row[4], constraints=row[5], expected_observations=row[6])
        cur.execute("SELECT content_digest FROM event_observation WHERE content_digest IS NOT NULL")
        resolvable = {r[0] for r in cur.fetchall()}
    return verify_attestation(att, issuer_pub_pem=issuer_pub_pem, expected_issuer=expected_issuer,
                              recomputed_semantics_digest=recomputed, now=now,
                              resolvable_digests=resolvable, revoked=revoked)
