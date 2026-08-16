# Kawa Identity and Credential Lifecycle v0.1

Status: Draft, normative security-plane contract
Scope: Day-0 trust bootstrap, Node identity, Workload identity, access credentials, JWKS, proof-of-possession, rotation, revocation, recovery, and offline limits.

## 1. Purpose

The Security Model requires infrastructure-attested identity before semantic processing.

This document defines one concrete v0.1 production profile. Future profiles may replace the mechanics without changing the security invariants.

> **Identity is attached, never declared.**

Security-plane state is not Domain Event state.

## 2. Identity hierarchy

```text
Human Administrator
        ↓ authorizes one-time enrollment
Kawa Trust Authority
        ↓ registers Node public key
TPM-backed Node Identity
        ↓ hosts attested runtime
Attested Runtime
        ↓ creates/binds Workload key
Workload Identity
        ↓ receives short-lived PoP-bound token
Authenticated Kawa request
```

Logical Agent identity remains descriptive and MUST NOT become the authorization root.

## 3. Trust Authority

A conforming v0.1 production deployment MUST have an explicit Trust Authority responsible for:

```text
one-time Node enrollment tokens
Node public-key registration
Workload credential issuance
JWT signing key lifecycle
JWKS publication
revocation state
credential policy
recovery
```

The Trust Authority MAY be implemented by an external identity system, but the behavior in this profile is normative.

## 4. Cryptographic profile

The v0.1 production profile uses:

```text
Trust-Authority JWT signing algorithm: Ed25519 / JOSE alg=EdDSA
JWT verification-key distribution: JWKS
Workload token lifetime: short-lived, deployment policy bounded
HTTP/MCP proof of possession: DPoP-style signed proof bound by cnf.jkt
Node key: asymmetric key protected by TPM/equivalent when available
```

A deployment MUST explicitly reject algorithms outside its configured profile. Token-controlled algorithm negotiation is prohibited.

A future Kawa security-profile version may select a different approved algorithm without changing Domain semantics.

## 5. Day-0 Node bootstrap

v0.1 uses one canonical enrollment flow:

```text
1. Human Administrator authenticates to the Trust Authority.
2. Administrator creates a single-use, short-lived Node enrollment token.
3. The new Node generates an asymmetric Node key pair locally.
4. Where available, the Node private key is TPM-backed/non-exportable.
5. The Node submits:
     enrollment token
     Node public key
     requested Node metadata required by policy
     hardware attestation evidence when policy requires it
6. Trust Authority validates and consumes the enrollment token atomically.
7. Trust Authority registers the Node public key and issues the Node identity record/credential.
8. The enrollment token can never be reused.
```

A Node MUST NOT become trusted merely by presenting a self-generated identifier or key.

If TPM/equivalent protection is unavailable, policy MUST explicitly classify the Node at a weaker assurance level; the absence of TPM is not silently ignored.

## 6. Node identity

Node identity answers:

> Which enrolled machine is participating?

Node identity MUST be established from the registered Node key/credential by attested infrastructure.

An Agent MUST NOT supply authoritative `origin_node`.

Node credentials MUST be rotatable and revocable.

Node-key replacement requires an authenticated rotation procedure or re-enrollment; a process cannot silently substitute a new Node key.

## 7. Workload identity

Workload identity answers:

> Which attested runtime process/service is making this request?

The attested runtime derives Workload identity from deployment policy and launches the Workload under that identity.

For each Workload credential context, the runtime establishes a Workload proof key inaccessible to ordinary LLM text/tool arguments.

A Workload identity binds, as applicable, to:

```text
Node identity
workload/service name
runtime policy
allowed audience
capability policy
credential lifetime
proof public key thumbprint
```

The LLM cannot choose its own attested Workload identity or proof key.

## 8. Access-token profile

The Trust Authority issues a short-lived signed JWT containing at least:

```text
iss   configured Trust Authority issuer
aud   Kawa service audience
sub   registered Workload identity
exp   expiration
iat   issued-at
jti   unique token identifier
cnf.jkt  thumbprint of Workload proof public key
```

`nbf` MAY be included when needed by deployment policy.

The signing key is Ed25519 and the JOSE algorithm is `EdDSA` for this v0.1 profile.

A copied JWT string without the matching Workload proof private key MUST NOT authenticate a production request.

## 9. Proof of possession

For HTTP/MCP requests, v0.1 uses a DPoP-style proof bound to the token's `cnf.jkt`.

For each protected request the client/runtime signs a proof covering at least:

```text
HTTP method
canonical target URI
iat
unique proof identifier
access-token binding/hash as required by the implementation profile
```

The server MUST verify:

```text
proof signature
proof public-key thumbprint == token cnf.jkt
method/URI binding
freshness
proof replay protection within configured window
access-token validity
```

A transport that cannot support this exact HTTP proof MUST define an equivalent authenticated-channel/request-signing binding before it may be used in the production profile.

## 10. JWT validation

A verifier MUST validate:

```text
signature with an approved JWKS key
alg == EdDSA for v0.1 production profile
known/active kid
configured issuer
configured audience
expiration
not-before when present
required claims
registered Workload resolution
cnf.jkt proof binding
revocation/authority state within freshness bounds
```

Successful parsing or signature verification alone is not authentication.

## 11. JWKS

The Trust Authority MUST publish current public JWT verification keys through its configured authenticated/pinned issuer metadata and JWKS location.

JWKS contains public verification material only. It does not convey authorization.

Verifiers MUST cache keys only for bounded periods and MUST reject unknown/retired `kid` values outside the rotation overlap.

## 12. Signing-key rotation

Signing keys MUST rotate without simultaneous restart of all Kawa Nodes.

Canonical sequence:

```text
1. generate new Ed25519 signing key
2. publish new public JWK with new kid
3. allow verifiers to observe new key
4. begin signing new tokens with new kid
5. retain old public key for bounded overlap
6. after maximum old-token lifetime + clock skew, reject old kid
7. remove old public JWK
8. securely retire old private key
```

Private signing keys MUST NOT be stored in Domain Events.

Compromise permits immediate emergency retirement rather than normal overlap.

## 13. Credential lifetime

Workload access tokens MUST be short-lived.

Exact lifetime is deployment policy, but it MUST be shorter than the maximum acceptable loss-of-revocation-connectivity interval for the granted risk class.

Long-lived bearer access tokens are non-conforming for production Workloads.

## 14. Revocation

Revocation is mandatory for the production profile.

Kawa MUST be able to revoke at least:

```text
Node identity
Workload identity
capability binding
Human Approval
break-glass authority
JWT signing key
```

Per-token `jti` revocation MUST be available for incident response even though normal containment SHOULD revoke the durable authority root and rely on short token lifetime.

A revoked Workload MUST NOT obtain a fresh token.

A verifier MUST NOT treat revocation-state unavailability as unlimited continued validity.

## 15. Revocation propagation

Connected verifiers MUST refresh authority/revocation state within a configured bounded interval.

v0.1 permits implementation by:

```text
signed revocation snapshots with short polling
plus short token lifetime
```

Push may be added as an optimization, but correctness MUST NOT depend on receiving every push notification.

The production configuration MUST publish its maximum revocation staleness bound.

## 16. Offline Nodes

Offline operation is supported only within explicit authority bounds.

An offline Node may rely on its last authenticated signed trust/revocation snapshot only until the configured staleness horizon.

When the horizon is exceeded:

```text
local/read-only low-risk behavior may continue if policy permits
high-risk mediated execution MUST be blocked
new approval-sensitive authority MUST NOT be assumed
new privileged Workload tokens MUST NOT be minted from stale authority state
```

Reconnect requires revocation/key reconciliation before privileged operation resumes.

## 17. Recovery

Loss or suspected compromise of Node credential material requires:

```text
revoke old Node identity/key
invalidate affected Workload authority
Human-authorized re-enrollment
issue/register fresh Node key
re-establish Workload proof keys and tokens
```

Recovery MUST NOT restore attested identity from conversation text, Domain Events, backup prompts, or self-asserted Agent state.

## 18. Human identity and Approval

Human authentication may be delegated to an external identity provider.

High-risk Kawa Approval remains a separate cryptographic authorization act and follows `approval-binding-v0.1.md`.

Login session possession alone is not Approval.

## 19. Request-side field minimization

Attested identity/security fields are not caller options.

An LLM-facing request MUST NOT require or accept authoritative values for:

```text
origin_node
workload_ref
actor_ref when actor is authenticated Workload
observer_ref for deterministic collectors
issuer
audience
kid
cnf.jkt
security timestamps
capability bindings
revocation state
```

These are attached/verified by attested infrastructure.

## 20. Security-plane storage

Security-plane state includes, as needed:

```text
Node registrations and public keys
Workload registrations/proof-key bindings
JWT public verification keys
revocation state
capability bindings
credential issuance/audit records
used/revoked enrollment tokens
security audit records
```

These are not ordinary Domain Events.

They MUST be auditable and SHOULD be correlatable with Domain Events without becoming Domain SoT.

## 21. Failure behavior

If identity verification, proof-of-possession, issuer/JWKS validation, or required revocation freshness cannot establish trust, protected semantic processing fails closed.

Invalid authentication receives no Wizard.

## 22. Acceptance tests

```text
A self-declared Workload identity is ignored.
A Node cannot enroll without a valid single-use Human-authorized enrollment token.
Reusing an enrollment token fails.
A copied JWT without the bound proof key fails authentication.
A token using any algorithm other than the configured v0.1 EdDSA profile is rejected.
A new signing key can be introduced without global downtime.
An old signing key stops validating after bounded overlap.
A revoked Workload cannot obtain a fresh token.
A revoked jti can be immediately denied during incident response.
An offline Node cannot retain privileged authority indefinitely.
A compromised Node can be revoked without deleting Domain history.
An Agent never needs access to private signing, Node, or Workload proof keys.
```

## 23. Core rule

> **Enroll once. Bind keys. Issue briefly. Prove possession. Rotate safely. Revoke decisively. Never let the Agent declare its own trust.**
