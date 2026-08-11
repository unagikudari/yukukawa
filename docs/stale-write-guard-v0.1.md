# Kawa Stale-Write Guard v0.1

Status: Draft, normative internal correctness contract
Scope: Deterministic detection of writes based on stale semantic state without exposing concurrency mechanics to ordinary LLM callers.

## 1. Purpose

Kawa must detect when a caller acts on an obsolete view of a mutable semantic object.

The caller should not manage database row versions, ETags, CAS values, or revision counters directly.

> **The caller states intent. Kawa proves whether that intent still applies to the current state.**

## 2. Public versus internal contract

Public behavior remains semantic:

```text
plan_changed
problem_reframed
review_stale
approval_stale
state_changed
```

Internal enforcement uses an opaque `basis` captured by Kawa when current state is read or Work is issued.

The ordinary LLM does not construct or interpret the basis.

## 3. Basis

For each state-dependent action, Kawa derives an internal basis from the Event frontier and trusted context that produced the action opportunity.

Conceptually:

```text
basis
  = subject
  + intended semantic action/event family
  + Project/scope
  + relevant Work/Review/Finding context
  + semantic projection version
  + latest applicable Event frontier
  + policy/schema identity where correctness depends on them
```

A basis is not a session-global version number.

It MUST be bound to the exact semantic context in which it may be reused. A basis for one Plan, Finding, Work item, or action MUST NOT accidentally authorize a different one.

Implementation MAY encode this as:

```text
opaque continuation token
semantic fingerprint
event frontier identifier
subject-local sequence
server-side work-context record
content hash over canonical semantic fields
```

The representation is replaceable. The comparison semantics are not.

## 4. Automatic propagation

Whenever Kawa exposes current state that may later authorize a state-dependent write, Kawa MUST establish an exact trusted association between that continuation and its basis.

This applies to relevant results from:

```text
kawa.get
kawa.bootstrap
kawa.work.next
Wizard guidance
```

The implementation MAY keep the basis server-side and return an opaque continuation handle, or return a protected opaque continuation token. It MUST NOT leave propagation unspecified.

A general conversational/session slot MUST NOT be used when concurrent operations could cause one action's basis to overwrite or substitute another's.

When the caller subsequently emits a state-dependent write, the runtime MUST recover and attach the exact matching basis automatically.

The LLM MUST NOT be required to provide or interpret:

```text
expected_revision
etag
row_version
cas_token
basis_hash
```

If Kawa cannot unambiguously identify the required trusted basis, it returns `needs_selection`, `precondition_failed`, or another semantic outcome rather than guessing.

## 5. Write rule

Before accepting a state-dependent write:

```text
1. authenticate
2. authorize
3. resolve subject and scope
4. identify the intended semantic action
5. recover the matching trusted basis for that exact action/work context
6. compute current basis
7. compare applicability
8. accept or return semantic conflict
```

A state-dependent write MUST NOT proceed without a valid matching basis.

If the requested Event is explicitly classified as state-independent, no prior basis is required solely for concurrency purposes.

If the requested Event remains valid independent of intermediate state, an unrelated stale projection does not invalidate it.

If the Event's meaning depends on previous state, mismatch MUST reject or require re-evaluation.

## 6. Examples

### Plan revision

A worker reads Plan A, receives Work to address Finding F, then another worker revises Plan A first.

The stale worker attempts its revision.

Kawa returns:

```yaml
status: conflict
reason: plan_changed
next_allowed_actions:
  - get_current_plan
  - reconsider_revision
```

### Observation

A deterministic collector records a newly observed hostname.

This Observation does not depend on the previous hostname Fact remaining unchanged.

A stale Fact projection is therefore not by itself a reason to reject the Observation.

### Finding resolution

Resolving Finding F depends on the current Finding and relevant Plan/Review basis.

If the Plan or Review binding changed, Kawa returns a semantic stale/conflict result rather than accepting a resolution against obsolete context.

### Concurrent work

The same Workload may hold Work for Plan A and Plan B concurrently.

A continuation/basis issued for Plan A MUST NOT be accepted for a Plan B revision even if both operations occur in the same authenticated session.

## 7. Work binding

`kawa.work.next` is the natural place to establish a trusted execution basis.

Internal Work coordination may bind:

```text
work identity
project
problem
plan
review
finding
allowed semantic output/action
required semantic frontier
policy version
capability context
```

The Agent receives semantic Work, not these mechanical fields.

Worker loss does not transfer stale authority. A newly issued Work item receives a new current basis.

Completing or releasing Work invalidates continuation state that should no longer authorize a state-dependent write.

## 8. Approval interaction

Approval binding is stricter than generic stale-write protection.

Even if a write is concurrency-safe, an action requiring Human Approval MUST independently satisfy `docs/approval-binding-v0.1.md`.

Concurrency validity does not revive stale Approval.

## 9. Federation

A Node that cannot establish a sufficiently current basis for a security-sensitive write MUST NOT guess.

Offline policy may permit explicitly bounded operations, but reconnect reconciliation must compare Event frontiers before accepting state-dependent continuation.

A basis created on one Node MUST NOT silently become valid on another Node unless federation policy can verify its authenticated origin, scope, and applicable Event frontier.

## 10. Acceptance tests

```text
Two workers read the same Plan; the second stale revision is rejected semantically.
An LLM never needs to invent an expected revision number.
A state-dependent write cannot proceed when Kawa cannot recover its exact basis.
A new independent Observation is not rejected merely because an unrelated projection changed.
A Finding cannot be resolved against a Review/Plan binding that has changed.
A new Work assignment receives a current basis after an earlier worker disappears.
A basis for Plan A cannot authorize a write to Plan B.
Two concurrent Work items in one authenticated session cannot exchange bases.
A released/completed Work continuation cannot be reused for a state-dependent write.
Changing the internal basis encoding does not change public MCP semantics.
```

## 11. Core rule

> **Hide concurrency tokens. Bind context exactly. Propagate basis deterministically. Preserve concurrency correctness.**
