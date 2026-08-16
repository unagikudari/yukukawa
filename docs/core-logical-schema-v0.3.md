# Kawa Core Logical Schema v0.3

Status: Draft, current normative candidate
Supersedes: `core-logical-schema-v0.2.md`

## 1. Purpose

This revision aligns the public logical model with Event Taxonomy v0.2, Reducer/Projection Contract v0.2, PostgreSQL Physical Schema v0.3, deterministic Observation ingestion, epistemic Claims, scope resolution, and Approval binding.

The schema should explain the system without requiring Kawa-specific training.

> **Expose semantic concepts. Hide mechanical concepts. Require only values the caller must actually decide.**

## 2. Source of Truth

Events are Kawa's only durable Domain Source of Truth.

Current objects are reconstructed views of Event history.

```text
Events
  ↓ reducers + policy + time
Current Understanding
```

No current-state table is authoritative.

## 3. Public concepts

```text
Project
Problem
Plan
Observation
Claim
Fact
Review
Finding
Approval
Work
Result
```

The epistemic core is:

```text
Observation = what was observed/measured/received
Claim       = what an accountable actor asserts or infers
Fact        = what current evidence and authority policy accepts as true
```

These meanings MUST NOT collapse into each other.

## 4. Enduring identities

A concept receives an enduring independent identity only when it has an independent lifecycle across multiple Events.

```text
Project  → enduring entity
Problem  → enduring entity
Plan     → enduring entity
Review   → enduring entity
Finding  → enduring entity

Observation → Event identity is sufficient
Claim       → Event identity is sufficient
Approval    → grant Event identity is sufficient by default
Result      → Event identity is sufficient by default
```

Test:

> **Does this concept need to live beyond one Event as an independently evolving thing?**

If no, do not invent another ID.

## 5. Project

A Project is the durable coordination scope for related Problems, Plans, and outcomes.

```yaml
Project:
  ref: kawa://project/...
  name: string
  purpose: string
  state: active | ended
```

Project scope may be automatically attached only when authority-bearing context resolves exactly one authorized Project.

Omission never means global scope.

See `scope-resolution-v0.1.md`.

## 6. Problem

A Problem is the current structured statement of something requiring explanation, decision, or action.

```yaml
Problem:
  ref: kawa://problem/...
  statement: string
  rationale: string | omitted
  state: active | resolved
  revalidation: clear | required
  evidence: [ref]
```

Reframing keeps the same Problem identity when the underlying issue remains the same.

The Agent does not manage ProblemRevision objects.

## 7. Plan

A Plan is the current intended course of action.

```yaml
Plan:
  ref: kawa://plan/...
  objective: string
  rationale: string | omitted
  state: draft | reviewing | approval_required | ready | running | blocked | ended
  revalidation: clear | required
  approval: not_required | required | pending | granted | stale | revoked | expired
  evidence: [ref]
  addresses: [Problem ref]
```

### 7.1 Plan structure

Kawa Core does NOT define an independent `PlanStep` entity in v0.3.

Plan structure may be represented as:

```text
small typed Plan content
external Git-managed artifact
external domain-specific workflow/resource
```

A future Step entity requires demonstrated independent lifecycle semantics before it is added to Core.

### 7.2 Plan changes

The public action is simply to revise the Plan.

Revision numbers, optimistic concurrency, stale-write basis, semantic fingerprints, and Approval binding are internal mechanics.

## 8. Observation

An Observation records a measured, received, or deterministically collected value about a subject.

The Event itself is the durable Observation record.

```yaml
Observation:
  ref: kawa://event/...
  subject: ref
  predicate: string
  value: typed value
  unit: string | omitted
  observed_at: timestamp
  observer: infrastructure-attested workload ref
  observation_method: string
```

Meanings:

```text
subject            = what was observed
observer           = authenticated Workload that observed it
observation_method = attested deterministic tool/method that produced it
predicate          = property observed
value              = observed value
```

`observer` and `observation_method` are attached by attested execution context, not free-form Agent claims.

An LLM inference is not an Observation merely because it refers to observed evidence.

See `deterministic-observation-ingestion-v0.1.md`.

## 9. Claim

A Claim is an accountable assertion or inference about a subject.

The Event itself is the durable Claim record.

```yaml
Claim:
  ref: kawa://event/...
  subject: ref
  claimant: authenticated actor ref
  predicate: string
  value: typed value | unknown
  rationale: string | omitted
  evidence: [ref]
  state: current | corrected | superseded | historical
```

A Claim is not automatically Fact.

A later change of view is another `claim.recorded` Event linked with `corrects` or `supersedes`; the original Event remains immutable.

Claimant identity is infrastructure-attested. The Agent does not choose `claimant` as authority.

See `epistemic-claim-model-v0.1.md`.

## 10. Fact

Fact is Kawa's current effective interpretation of what appears true given applicable Observations, Claims, Results, other evidence, and authority policy.

Fact is derived, not directly rewritten by an Agent.

```yaml
Fact:
  subject: ref
  predicate: string
  value: typed value | unknown
  state: clear | conflicted | unknown
  evidence: [ref]
```

An Agent that wants to change current understanding emits accountable evidence:

```text
deterministic measurement → observation.recorded
inference/assertion       → claim.recorded
execution outcome         → result.recorded
```

The Reducer reconstructs Fact.

If evidence conflicts and policy cannot resolve it, Fact remains `conflicted`.

## 11. Review

A Review is an independent challenge of a Plan.

```yaml
Review:
  ref: kawa://review/...
  plan: kawa://plan/...
  kind: adversarial | security | schema | operational | other
  state: active | completed | stale
  verdict: pass | changes_required | blocked | omitted
  findings: [Finding ref]
```

Review applicability to the current Plan is derived mechanically.

## 12. Finding

A Finding is a specific concern raised by Review.

```yaml
Finding:
  ref: kawa://finding/...
  severity: low | medium | high | critical
  type: string
  statement: string
  state: open | fixed | accepted_risk | rejected
  evidence: [ref]
```

High/critical open Findings normally block Plan readiness.

## 13. Approval

Approval means an authorized Human approved an exact action scope.

The grant Event is the Approval identity by default.

```yaml
Approval:
  ref: kawa://event/...
  plan: kawa://plan/...
  state: valid | stale | revoked | expired
  expires_at: timestamp | omitted
```

The Agent does not supply approval hashes, authority identity, semantic fingerprint, binding selection, or validity state.

Kawa binds and verifies mandatory authority scope according to `approval-binding-v0.1.md`.

## 14. Work

Work is a current claimable opportunity derived from semantic state and policy.

```yaml
Work:
  ref: kawa://work/...
  kind: string
  project: Project ref
  problem: Problem ref | omitted
  plan: Plan ref | omitted
  state: ready | claimed | blocked
  why: string
  next_allowed_actions: [string]
```

Work is not durable Domain truth.

Lease TTL, fencing, heartbeat, retry count, and stale-write basis mechanics are internal.

Plans persist; workers do not.

## 15. Result

A Result Event records a meaningful outcome of work, execution, investigation, verification, or decision.

```yaml
Result:
  ref: kawa://event/...
  outcome: success | failure | partial | inconclusive
  summary: string
  result_of: [ref]
  evidence: [ref]
```

The Event identity is sufficient unless Results later demonstrate an independent lifecycle.

## 16. Caller input minimization

Public writes require only semantic values that cannot be determined safely from authority-bearing context.

Attested infrastructure SHOULD automatically attach values such as:

```text
new opaque entity refs
node identity
workload identity
actor/observer identity when implied
Project scope when uniquely resolved
recorded time
schema version
causation/correlation when known
observation method from the attested collector path
stale-write basis
approval binding metadata
semantic links implied by current Work
```

If one value cannot be inferred uniquely, Kawa asks for that value only.

If multiple semantic choices remain, Kawa returns `needs_selection` rather than guessing.

See `llm-write-input-minimization-v0.1.md` and `mcp-contract-v0.2.md`.

## 17. Hidden correctness machinery

Kawa may internally maintain:

```text
revision sequence
semantic fingerprint
optimistic concurrency/stale basis
leases
fencing tokens
idempotency keys
execution ledger
projection cursors
revocation/security state
approval cryptographic proof
reconciliation state
```

These are not normal LLM reasoning concepts.

## 18. Security-plane boundary

Node, Workload, Credential, proof-of-possession keys, signing key, revocation, capability binding, and private audit/security state are Security-plane concepts.

They do not need to become Domain entities merely to be explicit.

Their lifecycle is defined separately in `identity-credential-lifecycle-v0.1.md`.

```text
Domain history
≠ Security authority state
```

## 19. External systems

Kawa does not duplicate mature external systems when references are sufficient.

```text
Git repository/commit/PR → Git remains artifact SoT
Ansible/scanner output   → attested collector establishes Observation
external side effect     → attested Adapter performs execution
```

Kawa records organizational meaning and links to external authoritative artifacts/results.

## 20. Acceptance tests

```text
A new LLM understands the public concepts from their names.
No public PlanStep lifecycle is required.
Fact cannot be rewritten directly as authoritative current state.
An LLM inference enters as Claim rather than deterministic Observation.
A Claim does not automatically become Fact.
Observation provenance cannot be self-declared by an Agent.
Claimant authority cannot be self-declared by an Agent.
Scope omission cannot widen visibility.
Approval validity cannot be self-declared by an Agent.
Current objects can be rebuilt from Events and policy.
Security authority remains explicit without becoming Domain SoT.
```

## 21. Core rule

> **Observe what happened. Claim what you infer. Derive what is accepted as Fact. Decide explicitly. Preserve the Event.**
