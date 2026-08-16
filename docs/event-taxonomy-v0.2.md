# Kawa Event Taxonomy v0.2

Status: Draft, current normative candidate
Supersedes: `event-taxonomy-v0.1.md`

## 1. Rule

Stable Domain Events describe durable organizational meaning, not implementation mechanics.

> **Explicit semantics. One canonical verb. No duplicate identity.**

Every Event has one semantic subject in `events.subject_ref`.

An enduring entity gets its own stable reference only when it has an independent lifecycle across multiple Events.

```text
Project   -> enduring entity
Problem   -> enduring entity
Plan      -> enduring entity
Review    -> enduring entity
Finding   -> enduring entity

Observation -> Event is the durable record
Claim       -> Event is the durable record
Approval    -> grant Event is the durable record by default
Result      -> Event is the durable record by default
```

## 2. Stable families

```text
project.*
problem.*
plan.*
observation.*
claim.*
review.*
finding.*
approval.*
result.*
authority.*
```

`Fact` and `Work` remain projections, not stable Domain Event families.

Epistemic distinction:

```text
Observation = what was observed/measured/received
Claim       = what an accountable actor asserts or infers
Fact        = what current evidence/authority policy treats as true
```

A Claim does not become Fact merely because it was recorded.

## 3. Event semantics

### Project

```text
project.created
project.updated
project.ended
```

`subject_ref` = Project.

### Problem

```text
problem.raised
problem.reframed
problem.resolved
```

`subject_ref` = Problem.

Typical links:

```text
based_on -> evidence Event/Fact/Resource
supports -> evidence
```

### Plan

```text
plan.proposed
plan.revised
plan.started
plan.ended
```

`subject_ref` = Plan.

Typical links:

```text
addresses -> Problem
based_on  -> evidence
```

`plan.ready`, revision tokens, CAS values, approval hashes, and lease state are not Domain Events.

### Observation

```text
observation.recorded
observation.corrected
```

Observation is a durable Event record, not an independently identified entity.

For `observation.recorded`:

```text
subject_ref          = thing observed
observer_ref         = authenticated observer
observation_method   = attested deterministic method/tool
predicate            = observed property
value_*              = observed value
```

For `observation.corrected`:

```text
corrects -> original Observation Event
```

Observation MUST NOT be used merely because an LLM inferred a proposition from evidence. Inference belongs in Claim.

### Claim

```text
claim.recorded
```

A Claim is an accountable assertion or inference about a subject. The Event itself is the durable Claim record; no independent Claim entity is required by default.

```text
subject_ref = thing the claim concerns
actor_ref   = authenticated Human/Workload making the claim
predicate   = property being asserted
value_*     = asserted value, including explicit unknown when applicable
rationale   = optional explanation
```

Typical links:

```text
based_on   -> Observation / Claim / Result / other evidence
supports   -> evidence
corrects   -> prior Claim Event
supersedes -> prior Claim Event
```

A later change of view is another `claim.recorded` Event linked to the prior Claim. Historical Claims are never edited.

Self-reported rationale or confidence is assertion content, not authority.

### Review

```text
review.started
review.completed
```

`subject_ref` = Review.

Typical link:

```text
reviews -> Plan
```

Reviewer = `actor_ref`.

### Finding

```text
finding.raised
finding.resolved
```

`subject_ref` = Finding.

Typical links:

```text
based_on -> Review
supports -> evidence
```

### Approval

```text
approval.granted
approval.revoked
```

By default, Approval is represented by its grant Event rather than a separate Approval entity.

For `approval.granted`:

```text
subject_ref = Plan being approved
actor_ref   = Human Principal granting approval
```

For `approval.revoked`:

```text
subject_ref = Plan
revokes     -> approval.granted Event
```

Cryptographic scope/revision binding belongs to the Security plane.

### Authority

```text
authority.configuration
authority.receipt
```

Added by roadmap step 10 (#118, realizing the FROZEN `consistency-and-authority-v0.1.md`):
authority is **event-sourced**. `authority.configuration` records one link of a key's
succession chain (genesis or proven successor — the founding/parent members' signatures
ride in the payload); `authority.receipt` records a CP operation's acceptance carrying an
accountable quorum proof. Both are PROOF material: their admission proves provenance and
replicates the verifier's chain source; **authority standing is computed by the
three-state verifier at read time, never by a reducer** — no projection moves on these
events, and a Receipt's presence in the log is not authority.

For `authority.receipt`:

```text
operation_digest  = canonical machine-stable bytes ONLY (never free prose)
quorum_proof      = signer set + individual signatures (accountable; bare aggregates
                    are non-conforming — slab §8)
```

### Result

```text
result.recorded
```

The Event itself is the durable Result record.

Typical link:

```text
result_of -> Plan / execution / prior Event
```

Large outputs are referenced as artifacts/resources.

## 4. Stable relation vocabulary

Initial semantic relations:

```text
addresses
based_on
supports
reviews
corrects
resolves
supersedes
result_of
revokes
```

A relation must read naturally as a sentence.

Avoid:

```text
related_to
associated_with
has_ref
misc
other
```

A new relation should be added only when an existing relation would materially distort meaning.

## 5. Not Domain Events

These are derived state or internal mechanics:

```text
plan.ready
plan.needs_revalidation
review_required
review.stale
approval.valid
approval.stale
fact.current
fact.conflicted
work.ready
work.claimed
lease.expired
cursor.advanced
projection.updated
hook.sent
index.rebuilt
```

Coordination/audit storage may record operational facts separately without promoting them into stable Domain vocabulary.

## 6. Naming rules

Stable Event names use:

```text
<noun>.<past-tense verb>
```

Rules:

1. one canonical name for one meaning;
2. ordinary language over protocol jargon;
3. past-tense occurrence over command/state names;
4. no transport/database/model vocabulary;
5. no Event solely to mirror a derived state transition;
6. no synonym families such as updated/modified/patched/changed for the same semantic action.

Canonical verbs:

```text
created
updated
ended
raised
reframed
resolved
proposed
revised
started
recorded
corrected
completed
granted
revoked
```

## 7. Stable Event set

```text
project.created
project.updated
project.ended

problem.raised
problem.reframed
problem.resolved

plan.proposed
plan.revised
plan.started
plan.ended

observation.recorded
observation.corrected

claim.recorded

review.started
review.completed

finding.raised
finding.resolved

approval.granted
approval.revoked

result.recorded

authority.configuration
authority.receipt
```

Total: **22 stable Domain Event types**.

The count is descriptive, not a target. Fewer stable semantics are preferred when meaning remains complete.

## 8. LLM test

A capable model should understand the history without Kawa-specific mechanics:

```text
A system value was observed.
An Agent recorded a claim based on that evidence.
The current Fact was reconstructed.
A problem was raised.
A plan was proposed.
The plan was reviewed.
A finding was raised.
The plan was revised.
Approval was granted.
A result was recorded.
```

No lease, CAS, cursor, revision-object, approval-hash, or direct Fact-write vocabulary is needed.

## 9. Ten-year test

Before adding an Event type or relation, ask:

```text
Would this still describe a meaningful fact if the LLM, MCP, database,
Agent runtime, UI, and coordination algorithm were all replaced?
```

If not, it belongs outside the stable Domain taxonomy.

> **Stable semantics. Replaceable mechanics.**
