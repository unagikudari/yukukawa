# Kawa Reducer / Projection Contract v0.2

Status: Draft, current normative candidate
Supersedes: `reducer-projection-contract-v0.1.md`

## 1. Core equation

```text
Events + Policy + Time -> Current Understanding
```

A Projection is disposable. If it cannot be deleted and rebuilt from durable inputs, it has become an accidental Source of Truth.

## 2. Reducer contract

```text
reduce(previous_state, event, policy) -> new_state
```

Given the same applicable Events, ordering rules, schema versions, policy version, and reference data, Kawa MUST produce the same semantic Projection.

An LLM MUST NOT be required to reconstruct authoritative current state.

## 3. Identity rule

```text
Project   -> enduring ref
Problem   -> enduring ref
Plan      -> enduring ref
Review    -> enduring ref
Finding   -> enduring ref

Observation -> Event ID
Claim       -> Event ID
Approval    -> grant Event ID by default
Result      -> Event ID
```

Do not synthesize extra identities in projections merely for symmetry.

## 4. Subject rule

`subject_ref` means one thing everywhere:

> the thing the Event is about.

Examples:

```text
problem.raised      -> Problem
plan.revised        -> Plan
review.completed    -> Review
finding.raised      -> Finding
approval.granted    -> Plan being approved
observation.*       -> thing observed
claim.recorded      -> thing claimed about
```

Relationships not represented by the Event subject are reconstructed from typed semantic links.

## 5. Current-by-default

Normal reads exclude non-current material unless history is explicitly requested:

```text
superseded framing
ended working state
corrected/superseded evidence when no longer applicable
stale review
invalid/stale approval
retired material
obsolete evidence
```

History remains durable and retrievable.

## 6. Project Projection

Inputs:

```text
project.created
project.updated
project.ended
```

Output:

```yaml
Project:
  ref: kawa://project/...
  name: string
  purpose: string
  state: active | ended
  end_reason: completed | cancelled | superseded | null
```

## 7. Problem Projection

Inputs:

```text
problem.raised
problem.reframed
problem.resolved
based_on/supports links
evidence state
```

Output:

```yaml
Problem:
  ref: kawa://problem/...
  statement: string
  rationale: string | null
  evidence_refs: [ref]
  state: active | resolved
  resolution: solved | invalidated | superseded | null
  revalidation: clear | required
```

## 8. Plan Projection

Inputs:

```text
plan.proposed
plan.revised
plan.started
plan.ended
addresses/based_on links
Problem state
Review/Finding state
Approval state
policy
```

Output:

```yaml
Plan:
  ref: kawa://plan/...
  objective: string
  rationale: string | null
  root_cause: string | unknown
  addresses: [problem ref]
  evidence_refs: [ref]
  phase: draft | reviewing | approval_required | ready | running | blocked | ended
  end_reason: completed | cancelled | failed | superseded | null
  revalidation: clear | required
  open_findings: [finding ref]
  approval: not_required | required | pending | granted | stale | revoked | expired
  next_allowed_actions: [action]
```

`ready` is derived. No `plan.ready` Event exists.

A `plan.revised` causes deterministic reevaluation of Review applicability, Approval binding, evidence applicability, Work readiness, and stale-write basis.

## 9. Observation Projection

Observation is projected from Event records rather than an Observation entity.

Inputs:

```text
observation.recorded
observation.corrected
corrects links
```

Output:

```yaml
Observation:
  event_ref: kawa://event/...
  subject_ref: ref
  observer_ref: ref
  observation_method: string
  predicate: string
  value: typed value
  observed_at: timestamp
  state: current | corrected | expired | historical
  corrected_by: event ref | null
```

Correction preserves original Event provenance.

Observation represents observed/received/measured evidence, not an LLM inference merely dressed as observation.

## 10. Claim Projection

A Claim is an Event-backed accountable assertion, not truth.

Inputs:

```text
claim.recorded
based_on/supports links
corrects/supersedes links
```

Output:

```yaml
Claim:
  event_ref: kawa://event/...
  subject_ref: ref
  actor_ref: authenticated claimant ref
  predicate: string
  value: typed value | unknown
  rationale: string | null
  evidence_refs: [ref]
  state: current | corrected | superseded | historical
```

Rules:

```text
Claim != Observation
Claim != Fact
self-reported confidence != authority
historical Claim is immutable
```

A new Claim may correct or supersede a prior Claim through explicit links. No Claim row is edited in place.

## 11. Fact Projection

Fact is derived from applicable evidence and authority policy.

Inputs MAY include:

```text
Observations
Claims
Results
other typed Events
actor/source authority
scope
validity periods
conflict-resolution policy
```

Output:

```yaml
Fact:
  subject_ref: ref
  predicate: string
  state: clear | conflicted | unknown
  value: typed value | unknown
  evidence_refs: [event/ref]
  confidence: numeric | unknown
```

Rules:

```text
Observation != Fact
Claim != Fact
Agent assertion != automatic truth
absence of evidence != false
unknown != null != false != not_applicable
unresolved conflict remains explicit
```

If applicable Observation and Claim evidence disagree and policy cannot resolve the disagreement, Fact MUST expose `conflicted` rather than silently preferring the LLM or the tool.

## 12. Review Projection

Inputs:

```text
review.started
review.completed
reviews link
finding state
Plan changes
```

Output:

```yaml
Review:
  ref: kawa://review/...
  plan_ref: ref
  state: active | completed | stale
  verdict: pending | pass | changes_required | blocked
  findings: [finding ref]
```

`stale` is derived from Plan applicability, not a Domain Event.

## 13. Finding Projection

Inputs:

```text
finding.raised
finding.resolved
based_on/supports links
```

Output:

```yaml
Finding:
  ref: kawa://finding/...
  review_ref: ref
  severity: low | medium | high | critical
  type: string
  statement: string
  state: open | fixed | accepted_risk | rejected
  evidence_refs: [ref]
```

High/critical open Findings normally block Plan readiness.

## 14. Approval Projection

Approval is derived around `approval.granted` Event records and protected Security-plane binding metadata.

Inputs:

```text
approval.granted
approval.revoked
revokes link
Plan changes
current time
security binding metadata
policy
```

Output:

```yaml
Approval:
  grant_event_ref: kawa://event/...
  plan_ref: ref
  granted_by: human principal ref
  state: valid | revoked | expired | stale
  expires_at: timestamp | null
```

No independent Approval entity is required by default.

## 15. Result Projection

Each `result.recorded` Event is itself the durable Result record.

```yaml
Result:
  event_ref: kawa://event/...
  outcome: success | failure | partial | inconclusive
  summary: string | null
  result_of: [ref]
  artifact_refs: [ref]
```

## 16. Work Projection

Work is derived current coordination state, not Domain SoT.

```yaml
Work:
  ref: opaque derived ref
  kind: string
  project_ref: ref
  problem_ref: ref | null
  plan_ref: ref | null
  state: ready | claimed | blocked
  reason: string
  next_allowed_actions: [action]
```

LLMs do not need lease TTLs, fencing tokens, heartbeat counters, CAS mechanics, or stale-write basis internals.

`work.next` is a Query.

## 17. Situation Awareness

Situation Awareness is a purpose-specific authorized composition, not a truth table.

```text
SA(scope, purpose, principal)
  = current Problems
  + current Facts
  + relevant Observations
  + relevant Claims
  + active Plans
  + open Findings
  + important Results
  + provenance
```

A Brief is disposable representation.

## 18. Security ordering

This contract defers authoritative security rules to `security-model-v0.1.md` and related security-plane contracts.

Retrieval order MUST preserve:

```text
authenticate
→ authorize visibility/capability
→ scope/lifecycle filter
→ retrieve
→ rank
→ summarize / Wizard
```

Unauthorized state must not influence returned candidates.

## 19. Conflict

Conflict is first-class Projection state.

```yaml
state: conflicted
alternatives:
  - value: ...
    evidence_refs: [...]
  - value: ...
    evidence_refs: [...]
next_allowed_actions:
  - investigate
  - record_claim
```

Do not silently LWW semantic disagreement.

## 20. Wizard integration

When a requested transition cannot proceed, reducers/policy expose semantic state sufficient for deterministic Wizard guidance.

Example:

```yaml
status: conflict
reason: plan_changed
next_allowed_actions:
  - get
  - revise
```

## 21. Rebuildability

Every derived Domain Projection MUST pass:

```text
snapshot semantic state
→ delete projection
→ replay/reduce Events
→ compare semantic state
```

Implementation metadata need not match. Semantic meaning must.

## 22. Versioning

Keep distinct:

```text
Event schema version
Reducer/projection version
Policy version
```

Reducer changes never rewrite historical Event meaning.

## 23. No hidden LLM authority

LLM output becomes organizational input only through explicit accountable semantics.

```text
LLM infers proposition  -> claim.recorded
LLM proposes Plan       -> plan.proposed
LLM raises concern      -> finding.raised
Trusted tool observes   -> observation.recorded
Execution produces outcome -> result.recorded
```

A model's inference does not become truth merely because it appears in a projection.

## 24. LLM-friendly test

A projection should answer, without protocol archaeology:

```text
What is this?
What is its current state?
Why is it in that state?
What evidence supports it?
Was this observed or merely asserted?
What can this principal do next?
```

Prefer explicit semantic states over overloaded booleans and NULLs.

## 25. Core invariant

```text
Durable Events
  -> deterministic reducers
  -> current authorized understanding
  -> obvious next Work
  -> new durable Events
```

> **Observe what happened. Claim what you infer. Derive what is currently accepted as Fact.**
