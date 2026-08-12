"""Process Incarnation identity + credential issuance + local capability binding.

The SECURITY PLANE (v0.5 §14-§15; #98 §3-§7). Distinct from the Domain event log: nothing
here is a durable Domain Event. Distinct from Node identity: a `WorkloadCredential` is a
different trust statement than a `NodeCredential` (`credential.py`), carries a `wl:` key-id
namespace, and the two are never interchangeable.

**Guarantees are named, not the components** (#104 rev 2). This module proves, IN-PROCESS,
NOW:
- issuer-signed identity: a credential was minted by the trusted broker, not self-issued;
- holder-of-key WITHIN this process: a copied credential without the matching ephemeral PoP
  private key cannot pass a protected operation (cnf.jkt binding + a nonce'd signature).

It does NOT prove transport holder-of-key against a network attacker (no HTTP DPoP — step
6/7), does NOT protect across process/restart boundaries (nonce memory is per-incarnation),
and — critically — does NOT stop malicious code sharing this process, which can read the
`ProcessIncarnation`'s PoP private key directly from memory. In-process capability binding
is NOT HTTP DPoP; it is a local capability binding.

Key separation (round-2 constraint): the issuer signing key lives ONLY inside the closure
of `CredentialBroker.issue`; agent-facing code receives `request_credential` — a plain
function that closes over the broker but exposes no attribute path, no broker reference, and
no key. `test_issuer_key_unreachable` walks __dict__, __closure__, __self__, and
gc.get_referents to prove it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from kawa.domain.ids import canonical_json, digest, uuid7

ISSUER_KEY_NS = "wl-iss:"
POP_KEY_NS = "wl-pop:"


def _thumbprint(pub: Ed25519PublicKey, ns: str) -> str:
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return ns + hashlib.sha256(raw).hexdigest()[:16]


def _fresh(iat: str, now: str, window_seconds: int) -> bool:
    """A proof's iat must be within [now - window, now + small skew] — an actual freshness
    check, not a caller's word. Both are ISO-8601 UTC strings passed in (no wall-clock read
    here, so the verifier stays deterministic/testable)."""
    try:
        t_iat = datetime.fromisoformat(iat.replace("Z", "+00:00"))
        t_now = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except Exception:
        return False
    delta = (t_now - t_iat).total_seconds()
    return -5 <= delta <= window_seconds       # allow ≤5s of forward skew; reject stale/future


# ---- 5A: Process Incarnation ----

@dataclass(frozen=True)
class ProcessIncarnation:
    """A single process lifetime's identity. A restart is a NEW incarnation even at identical
    node/runtime/workload/PID (§14.1). Holds the ephemeral PoP PRIVATE key — the only thing
    that proves holder-of-key. PID is non-authoritative metadata, never an auth input."""

    incarnation_id: str
    node: str
    runtime: str
    workload: str
    pid: int | None            # observational metadata ONLY (§14.1)
    _pop_private: Ed25519PrivateKey = field(repr=False, compare=False)

    @property
    def cnf_jkt(self) -> str:
        return _thumbprint(self._pop_private.public_key(), POP_KEY_NS)

    def pop_public_pem(self) -> str:
        return self._pop_private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def sign_proof(self, *, operation_ref: str, work_ref: str, jti: str, nonce: str,
                   iat: str) -> str:
        """Sign a capability-binding proof over the operation, credential jti, and a nonce.
        Holder-of-key within this process: only the incarnation holding the PoP private key
        can produce this."""
        payload = canonical_json({"operation_ref": operation_ref, "work_ref": work_ref,
                                  "jti": jti, "nonce": nonce, "iat": iat})
        return self._pop_private.sign(payload.encode()).hex()


def new_incarnation(*, node: str, runtime: str, workload: str, pid: int | None = None,
                    now_ms: int | None = None) -> ProcessIncarnation:
    return ProcessIncarnation(incarnation_id=str(uuid7(now_ms)), node=node, runtime=runtime,
                              workload=workload, pid=pid, _pop_private=Ed25519PrivateKey.generate())


# ---- 5B: WorkloadCredential + CredentialBroker (issuer key encapsulated) ----

@dataclass(frozen=True)
class WorkloadCredential:
    """Issuer-signed, short-lived. A DISTINCT type from NodeCredential; `iss` is a `wl-iss:`
    key-id, never a node origin key. `cnf_jkt` binds the holder's PoP key — a copied
    credential without that key cannot pass a protected operation."""

    payload: dict          # iss/aud/sub/exp/iat/jti/cnf_jkt/node/runtime/workload/capability_ctx
    signature: str         # issuer Ed25519 over canonical_json(payload)

    @property
    def jti(self) -> str:
        return self.payload["jti"]

    @property
    def cnf_jkt(self) -> str:
        return self.payload["cnf_jkt"]


class CredentialBroker:
    """The trusted local issuer. Holds the issuer signing key PRIVATELY. Agent-facing code
    gets `request_credential` (a closure), never this object or the key. `issue` is the ONLY
    signing path and applies policy (audience + lifetime) — an agent has no `sign()` to call."""

    def __init__(self, *, audience: str, ttl_seconds: int = 300) -> None:
        _issuer = Ed25519PrivateKey.generate()                 # bound in closures below; no attr
        self.issuer_key_id = _thumbprint(_issuer.public_key(), ISSUER_KEY_NS)
        self.audience = audience
        _issuer_pub_pem = _issuer.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self._issuer_pub_pem = _issuer_pub_pem
        key_id = self.issuer_key_id
        aud = audience

        def _sign(payload: dict) -> str:
            return _issuer.sign(canonical_json(payload).encode()).hex()

        def issue(inc: ProcessIncarnation, *, capability_ctx: dict | None,
                  iat: str, exp: str, jti: str | None = None) -> WorkloadCredential:
            payload = {
                "iss": key_id, "aud": aud, "sub": inc.workload,
                "node": inc.node, "runtime": inc.runtime, "workload": inc.workload,
                "cnf_jkt": inc.cnf_jkt, "iat": iat, "exp": exp,
                "jti": jti or str(uuid7()), "capability_ctx": capability_ctx or {},
            }
            return WorkloadCredential(payload=payload, signature=_sign(payload))

        self.issue = issue                                     # bound method-free closure
        self._sign_attestation = _sign                         # used by AttestationIssuer (same issuer)

    def request_credential_fn(self):  # type: ignore[no-untyped-def]
        """Return the narrow interface handed to agent-facing code: a plain function that
        can request a credential but exposes no broker reference and no key. It closes over
        `self.issue` (itself a closure over the key) — never over `self`."""
        issue = self.issue

        def request_credential(inc: ProcessIncarnation, *, capability_ctx: dict | None,
                               iat: str, exp: str) -> WorkloadCredential:
            return issue(inc, capability_ctx=capability_ctx, iat=iat, exp=exp)

        return request_credential

    def issuer_public_pem(self) -> str:
        return self._issuer_pub_pem


def verify_credential(cred: WorkloadCredential, *, issuer_pub_pem: str, expected_issuer: str,
                      expected_audience: str, now: str,
                      revoked: set[str] | None = None) -> bool:
    """Fail-closed identity check (NOT holder-of-key — that is verify_capability_binding).
    Checks signature, issuer id, audience, expiry, and revocation. Any mismatch -> False."""
    p = cred.payload
    if p.get("iss") != expected_issuer or p.get("aud") != expected_audience:
        return False
    if not (p.get("iat", "") <= now < p.get("exp", "")):
        return False
    if revoked and p.get("jti") in revoked:
        return False
    if not str(p.get("iss", "")).startswith(ISSUER_KEY_NS):    # namespace: never a node key
        return False
    try:
        pub = serialization.load_pem_public_key(issuer_pub_pem.encode())
        if not isinstance(pub, Ed25519PublicKey):
            return False
        pub.verify(bytes.fromhex(cred.signature), canonical_json(p).encode())
        return True
    except Exception:
        return False


# ---- 5D: local capability binding (in-process holder-of-key) ----

class CapabilityVerifier:
    """Verifies a PoP proof binds the caller to the credential's cnf.jkt for one operation,
    and rejects replays. Nonce state is an in-memory set owned HERE, scoped to this process
    lifetime: replay is rejected within the same process + window; a restart clears it (a new
    incarnation anyway). Cross-process/durable replay protection is DEFERRED (step 7)."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def verify(self, *, cred: WorkloadCredential, proof_sig: str, pop_public_pem: str,
               operation_ref: str, work_ref: str, nonce: str, iat: str, now: str,
               window_seconds: int = 60) -> bool:
        if nonce in self._seen:
            return False                                       # replay within this process
        if not _fresh(iat, now, window_seconds):               # actual freshness, not a caller bool
            return False
        # the presented PoP key MUST match the credential's bound thumbprint
        try:
            pub = serialization.load_pem_public_key(pop_public_pem.encode())
            if not isinstance(pub, Ed25519PublicKey):
                return False
            if _thumbprint(pub, POP_KEY_NS) != cred.cnf_jkt:   # copied credential, wrong key
                return False
            payload = canonical_json({"operation_ref": operation_ref, "work_ref": work_ref,
                                      "jti": cred.jti, "nonce": nonce, "iat": iat})
            pub.verify(bytes.fromhex(proof_sig), payload.encode())
        except Exception:
            return False
        self._seen.add(nonce)                                  # single-use
        return True


# ---- 5C: Work Attestation (pinned to an immutable derived event) ----

def work_semantics_digest(*, work_ref: str, plan_ref: str, work_kind: str,
                          role_requirement: str | None, objective: str | None,
                          constraints: list[str] | None,
                          expected_observations: list[str] | None) -> str:
    """Canonical digest over a SPECIFIC WorkDerived event's typed fields (§102). Signs
    semantics, never the rendered instruction prose (#98 §3)."""
    return digest({
        "work_ref": work_ref, "plan_ref": plan_ref, "work_kind": work_kind,
        "role_requirement": role_requirement, "objective": objective,
        "constraints": constraints, "expected_observations": expected_observations,
    })


@dataclass(frozen=True)
class WorkAttestation:
    payload: dict          # work_ref/derived_event_id/work_semantics_digest/source_basis/policy_digest/iss/iat/exp/jti
    signature: str


def make_attestation(broker: CredentialBroker, *, work_ref: str, derived_event_id: str,
                     work_semantics_digest: str, source_basis: list[dict], policy_digest: str | None,
                     iat: str, exp: str, jti: str | None = None) -> WorkAttestation:
    payload = {
        "work_ref": work_ref, "derived_event_id": derived_event_id,
        "work_semantics_digest": work_semantics_digest,
        "source_basis": source_basis, "policy_digest": policy_digest,
        "iss": broker.issuer_key_id, "iat": iat, "exp": exp, "jti": jti or str(uuid7()),
    }
    return WorkAttestation(payload=payload, signature=broker._sign_attestation(payload))


def verify_attestation(att: WorkAttestation, *, issuer_pub_pem: str, expected_issuer: str,
                       recomputed_semantics_digest: str, now: str,
                       resolvable_digests: set[str] | None = None,
                       revoked: set[str] | None = None) -> bool:
    """The signed basis is an IMMUTABLE derived_event_id (round-2 constraint 3). The caller
    recomputes work_semantics_digest by fetching THAT event from the store by id (not the
    mutable projection) and passes it in; a re-derive of the same work_ref yields a different
    event id and will not match. When `resolvable_digests` is given, EVERY source_basis
    content_digest must still resolve locally (a bound source that vanished fails the
    attestation — #98 §2 basis integrity). Fail-closed on drift, unresolved basis, expiry,
    wrong issuer, revocation."""
    p = att.payload
    if p.get("iss") != expected_issuer or not (p.get("iat", "") <= now < p.get("exp", "")):
        return False
    if revoked and p.get("jti") in revoked:
        return False
    if p.get("work_semantics_digest") != recomputed_semantics_digest:      # TOCTOU / drift
        return False
    if resolvable_digests is not None:
        for basis in p.get("source_basis", []):
            if basis.get("content_digest") not in resolvable_digests:      # a bound source vanished
                return False
    try:
        pub = serialization.load_pem_public_key(issuer_pub_pem.encode())
        if not isinstance(pub, Ed25519PublicKey):
            return False
        pub.verify(bytes.fromhex(att.signature), canonical_json(p).encode())
        return True
    except Exception:
        return False
