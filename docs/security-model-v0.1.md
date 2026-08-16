# Kawa Security Model v0.1

Status: Draft, normative
Scope: Security assumptions, trust boundaries, identity, authorization, approval, secret/resource isolation, execution safety, and containment.

## 1. Purpose

This document is the canonical security model for Kawa.

Other specifications MAY summarize security behavior, but security requirements SHOULD be defined here and referenced rather than redefined independently.

Operational identity/credential mechanics are specified by `identity-credential-lifecycle-v0.1.md`; where this document states an invariant and that document defines its mechanism, both are normative and the stricter requirement applies.

Kawa is designed around a simple trust assumption:

> **An Agent may be authenticated and still be wrong, compromised, prompt-injected, or malicious.**

LLM goodness is not a security boundary.

## 2. Security invariants

```text
Authenticate before semantic processing.
Authorize before disclosure.
Identity is infrastructure-attested, not Agent-asserted.
A Resource handle is not authority.
Capabilities are explicit and scoped.
Secrets are mediated, not disclosed.
Human approval is cryptographic and binds exact scope.
Production authority is revocable.
Event replay never repeats external side effects.
Prompt text cannot grant authority.
Derived state cannot become authority by accident.
```

## 3. Identity layers

Kawa distinguishes four identities:

```text
Human Identity
Node Identity
Workload Identity
Logical Agent Identity
```

### 3.1 Human Identity

Human authorization originates from a Human Authenticator.

Human login and Human approval are separate operations.

High-risk approval MUST be attributable to a concrete Human Principal and cryptographic proof.

### 3.2 Node Identity

A Node is a human-managed machine participating in Kawa.

A TPM or equivalent hardware trust anchor SHOULD protect Node identity where supported.

TPM attestation establishes trust in the Node identity. It does not prove that an Agent is benign.

Root/kernel compromise is outside Kawa's host-security guarantee.

### 3.3 Workload Identity

Workload Identity is the primary NHI security boundary for Agent, collector, service, and adapter processes.

The OS/kernel and attested runtime establish workload identity.

An Agent MUST NOT choose, rewrite, or self-assert its infrastructure-attested workload identity.

Production Workload authentication MUST demonstrate possession of runtime-held credential material or an authenticated channel binding. Possession of a copied bearer-token string alone is insufficient.

### 3.4 Logical Agent Identity

Logical Agent Identity MAY identify a model/role/session for accountability and workflow policy, but MUST NOT replace Workload Identity as the authorization root.

## 4. Authentication gate

Protected semantic processing MUST occur only after successful authentication.

The v0.1 production profile uses signed short-lived Workload access credentials with asymmetric verification keys and proof-of-possession or authenticated-channel binding, as defined in `identity-credential-lifecycle-v0.1.md`.

JWT validation requires:

```text
signature
explicitly allowed algorithm
configured issuer
audience
expiration
not-before when present
required claims
principal/workload resolution
credential-possession/channel-binding proof
current revocation/authority state within policy freshness bounds
```

Revocation checking is not optional in the production security profile.

Successful decoding is not successful authentication.

Processing order:

```text
request
→ parse minimum authentication envelope
→ authenticate identity + possession
    invalid → minimal rejection
    valid   → authenticated principal
→ authorization
→ semantic processing
→ success / Wizard
```

Invalid authentication MUST NOT receive Wizard guidance, resource discovery, capability discovery, Project/Plan existence information, approval requirements, or semantic search results.

Authentication mechanisms are replaceable. The durable invariant is authenticated, revocable identity before semantic processing.

## 5. Authorization model

Authorization is evaluated over:

```text
Identity
+ Capability
+ Resource
+ Operation
+ Constraints
+ Approval when required
```

Conceptually:

```text
WHO
may do WHAT
against WHICH RESOURCE
under WHICH CONSTRAINTS
under WHICH AUTHORITY
```

Authentication does not imply authorization.

## 6. Capability model

Capabilities SHOULD use natural semantic names, for example:

```text
plan.review
plan.execute
resource.read
resource.modify
```

Avoid broad, ambiguous capabilities where narrower capability plus Resource selector can express the same authority.

Capability discoverability is separate from capability possession.

A valid principal MUST NOT automatically receive the full capability catalog.

Policy remains authoritative even when the Wizard advertises currently allowed actions.

## 7. Resource handles

A Resource Handle identifies WHAT the caller refers to.

It carries no authority by itself.

Resource Handles SHOULD be opaque and SHOULD avoid exposing unnecessary physical locators such as:

```text
host
IP address
port
filesystem path
database name
username
connection string
secret reference material
```

The Resource Resolver maps an opaque handle to operational details within the attested infrastructure boundary.

An Agent normally sees the handle and safe metadata, not the underlying locator.

## 8. Secrets

Agents SHOULD NOT receive raw credentials such as:

```text
API keys
passwords
SSH private keys
database credentials
service tokens
```

Secret-mediated execution is preferred.

An infrastructure-operated Secret Broker or Adapter may obtain protected credentials internally and perform an authorized operation on behalf of the workload.

Secrets at rest SHOULD be protected independently from Domain Events and MAY use TPM-sealed wrapping keys or equivalent mechanisms.

Secret material MUST NOT be persisted in Domain Event payloads for convenience.

## 9. Human approval

High-risk actions require Human approval according to policy.

High-risk Approval MUST bind at least:

```text
Plan identity
Plan semantic revision/fingerprint
resource / target scope
operation set
Human approving authority
expiry
```

Policy MAY additionally bind Review identity/state, requesting Workload, security-sensitive constraints, policy version, or other restrictions. Policy MUST NOT remove the mandatory minimum binding set for a high-risk Approval.

Any mismatch in a bound semantic value makes the Approval stale or inapplicable. No LLM judgment of whether a change is “material” is authoritative.

Natural-language claims such as "admin approved" are not approval.

High-risk examples include:

```text
schema change
migration
authentication change
authorization change
secret handling
resource resolver change
destructive database operation
break-glass operation
```

See `approval-binding-v0.1.md` for deterministic binding and TOCTOU rules.

## 10. Break-glass

Emergency authority MUST be separate from normal authority.

Break-glass MUST NOT mean unrestricted elevation.

It SHOULD bind:

```text
Human Principal
Workload
Node
Resource
Operation
Reason
Short TTL
Exact approved scope
```

Break-glass SHOULD automatically expire and relock.

Raw secret export is a last-resort emergency mechanism, not the normal execution path.

Emergency actions MUST remain highly visible in Event/Audit history.

## 11. Agent threat model

Kawa treats an Agent as an authenticated principal whose decisions carry no inherent authority — authenticity proves origin, not correctness or semantic authority.

Possible Agent failure modes include:

```text
prompt injection
model error
malicious instruction following
hallucinated authority
confused deputy behavior
unsafe plan generation
secret-seeking behavior
state poisoning attempts
```

Containment comes from typed state, policy, capability boundaries, approval, mediated execution, and independent review—not from trusting Agent intent.

Prompt text, retrieved documents, memory, or natural-language claims MUST NOT grant capabilities or approval.

## 12. Data versus instruction

Kawa state is typed data.

Retrieved content does not become executable instruction merely because it contains imperative language.

Examples:

```text
Observation = measured/received evidence
Claim = accountable assertion or inference
Standing = derived, recomputable support status of a Claim
Plan = proposed organizational decision
Approval = authority
Capability = authority boundary
```

These concepts MUST remain distinct.

A Claim is not authoritative merely because it came from an authenticated or privileged Workload. Claim authority and standing derivation are separate policy questions. (Kawa has no Kawa-owned Fact entity — current interpretation belongs to observers; v0.5 §2.)

## 13. Review and security challenge

Significant Plans SHOULD be independently challenged before execution.

For policies requiring independent review:

```text
Author workload != Reviewer workload
```

Security review includes at least:

```text
capability escalation
secret exposure
confused deputy
scope expansion
unsafe external execution
rollback failure
partial failure
verification weakness
```

No significant Plan should be executed by the same reasoning path that authored it without independent challenge when policy requires review.

## 14. External side effects

Kawa separates:

```text
Intent
Authorization
Execution
Observation / Result
```

These MUST NOT collapse into one statement.

An Agent saying "I executed X" is not equivalent to an Adapter confirming X occurred.

Event replay MUST NEVER repeat an external side effect.

Execution adapters require protected idempotency/reconciliation mechanics sufficient to handle:

```text
timeout
retry
duplicate request
success followed by Kawa write failure
unknown external outcome
partial failure
```

External execution uncertainty MUST be reconcilable against the external system.

## 15. Wizard security boundary

Wizard guidance is available only after successful authentication.

After authentication, guidance is filtered by authorization.

```text
401: identity not authenticated → no Wizard
403: identity authenticated, action forbidden → limited authorized guidance only
409: identity/action understood, state conflicts → authenticated Wizard guidance
```

`next_allowed_actions` MUST be principal-specific and MUST NOT advertise actions or resources the caller cannot discover or invoke.

## 16. Accountability

Attested infrastructure attaches authoritative accountability metadata.

Agents MUST NOT self-assert infrastructure-attested values such as:

```text
node identity
workload identity
capability
approval identity
security timestamps
```

Kawa SHOULD be able to answer:

```text
WHO did WHAT
WHERE / on WHICH NODE
AS WHICH WORKLOAD
UNDER WHICH AUTHORITY
BECAUSE OF WHICH prior event/request
FOR WHICH Project/Plan
```

Transport/audit logs and Domain Events are separate planes but SHOULD be correlatable.

## 17. Kill switches and revocation

A conforming production deployment MUST support rapid containment at multiple levels:

```text
revoke Node identity
revoke Workload identity
revoke capability
invalidate approval
stop mediated execution
freeze Resource access
revoke/retire signing authority
```

Containment MUST be possible without deleting Domain history.

Revocation propagation may be implemented by push, polling, signed snapshots, short-lived credentials plus authority refresh, or another mechanism, but stale privileged authority MUST be bounded by policy. See `identity-credential-lifecycle-v0.1.md`.

## 18. Federation and offline Nodes

Offline operation is normal, but offline authority is not unlimited.

Federation MUST preserve:

```text
immutable Events
explicit conflicts
revocation/tombstone semantics
security provenance
```

A stale offline Node MUST NOT silently resurrect revoked or retired authority/state on reconnect.

Production policy MUST define staleness horizons and offline authority limits for security-sensitive operations before such operations are enabled offline.

## 19. Search and disclosure ordering

Authorization and visibility filtering MUST precede semantic ranking.

Correct:

```text
authenticate
→ authorize visibility
→ scope/lifecycle filter
→ retrieve/rank
```

Incorrect:

```text
search everything
→ rank everything
→ filter response afterward
```

Unauthorized state MUST NOT influence candidate disclosure or Wizard selection results.

## 20. Security-plane separation

The following do not belong in ordinary Domain Event payloads:

```text
private keys
secret values
raw JWTs
TPM-sealed material
resource resolver mappings
credential material
authenticator private state
```

Security-plane data may have different storage, replication, backup, audit, and destruction requirements from Domain Events.

## 21. Public security design

Kawa MUST NOT depend on secrecy of its architecture, protocols, schemas, or security model for protection.

> **Kawa's security must remain effective when its architecture, protocols, schemas, and security model are public.**

Security derives from protected key material, authenticated identity, explicit authorization, scoped capabilities, cryptographically bound approval, mediated execution, and enforced trust boundaries—not from obscurity of design.

The following are intended to be safe to publish:

```text
security architecture
trust boundaries
identity model
capability model
JWT validation requirements
Wizard authentication boundary
approval model
break-glass principles
secret mediation model
side-effect separation
revocation and containment principles
```

Publication of those principles MUST NOT reduce the expected security of a conforming deployment.

This rule does not require publication of live defensive state or exploitable operational weakness.

## 22. Publication boundary

Runtime security state and operator-specific infrastructure belong in a deployed Kawa instance, not in the source repository.

Repository examples MUST use synthetic data.

The repository MUST NOT contain real credentials, local infrastructure, real Node identifiers, private addresses, hardware inventories, production connection strings, secret mappings, or unsanitized operational evidence.

The security publication boundary is:

```text
Security architecture / invariants → public
Secrets / live security state       → private
Operational weakness / exceptions   → private
```

Examples of material that remains private include:

```text
private keys
JWT signing keys
actual JWTs
live Node / Workload identities
current capability bindings
Resource Handle → physical locator mappings
Secret Broker mappings
approval proof material
revocation state
production topology
security logs containing exploitable operational detail
unpatched weaknesses
operational exceptions
```

See `docs/publication-boundary.md` for repository publication rules.

## 23. Trust boundary summary

```text
Human Authenticator
        ↓
Human Principal
        ↓ approval

TPM / Node trust
        ↓
Node Identity
        ↓
OS / kernel / attested runtime
        ↓
Workload Identity
        ↓ authenticated, no inherent authority
Agent
        ↓ semantic request
AuthZ / Policy / Capability / Approval
        ↓
Mediator / Adapter / Secret Broker
        ↓
External Resource
```

Authority flows downward through explicit attested boundaries. It does not flow upward from Agent text.

## 24. Non-goals

Kawa does not claim to defend against a fully compromised host kernel/root boundary.

Kawa does not treat TPM attestation as proof that an LLM or Agent is benign.

Kawa does not depend on prompt instructions as a security control.

Kawa does not expose secrets merely to make Agent integration easier.

Kawa does not use Event replay as an execution engine.

## 25. Acceptance tests

A conforming implementation should pass security tests equivalent to:

```text
Invalid authentication cannot discover protected state.
A copied access-token string without required proof-of-possession cannot authenticate as a production Workload.
An Agent cannot choose its attested workload identity.
A Resource Handle alone cannot authorize an operation.
A prompt cannot grant a capability.
A natural-language approval cannot authorize a high-risk operation.
Changing any approval-bound semantic value invalidates stale approval.
Revoked authority cannot obtain or retain privileged production access beyond configured freshness bounds.
Revoked authority is not resurrected by an offline Node.
Event replay cannot repeat an external side effect.
Unauthorized state cannot influence semantic search results returned to the caller.
A compromised/misled Agent remains constrained by capabilities and mediated execution.
Publishing the security architecture does not expose any secret required for enforcement.
```

## 26. Core rule

> **Authenticate identity. Authorize capability. Bind approval. Revoke authority. Mediate secrets. Contain the Agent. Preserve the Event.**
