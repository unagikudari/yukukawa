# Memory Broker Extend vs Kawa v0.1

Status: Active architecture decision gate
Purpose: Decide whether Kawa should extend Memory Broker or remain a greenfield Domain core with a migration boundary.

## 1. Decision question

The question is not whether Memory Broker already contains concepts analogous to Plan, Observation, Review, Work, or epistemic evidence typing.

The question is:

> Can Memory Broker adopt Kawa's architectural invariants with lower complexity and lower migration risk than introducing a separate greenfield Domain core?

## 2. Kawa invariants under test

```text
Events are the only durable Domain Source of Truth.
Current state is disposable and rebuildable.
One canonical Domain write primitive exists: Emit.
Observation / Claim / Fact remain semantically distinct.
LLM-facing semantics hide mechanical coordination state.
Identity/provenance is infrastructure-attached, not Agent-declared.
Authorization and scope filtering precede retrieval/ranking.
External side effects are not Event replay.
Plans persist; workers do not.
Git-native artifacts remain in Git.
Deterministic collectors establish Observation provenance.
Context is pulled, current-by-default, and minimal.
```

## 3. Evidence correction

Memory Broker already has meaningful epistemic typing in its live observation/evidence surface, including distinctions such as observed, derived, inferred, intended, and hypothesis.

Therefore:

> **Kawa is NOT justified by claiming Memory Broker lacks epistemic typing.**

That claim must not be used as greenfield evidence.

Kawa's Claim/Observation distinction is still a desired semantic contract, but existing Broker evidence typing is a reuse signal, not a rewrite argument.

## 4. Extension advantages

```text
existing deployed code and fleet knowledge
existing operational integrations
existing review/plan mechanisms
existing epistemic evidence typing
existing migration and reconciliation machinery
lower immediate cutover surface
```

## 5. Extension risks to prove or falsify

```text
generic memory/tag/content substrate remains load-bearing
multiple write/read paths may make one canonical semantic contract expensive
current-state and legacy paths may resist Event-only rebuildability
multi-writer replication constraints may shape Domain semantics
identity/provenance attachment may remain path-specific
compatibility may force permanent alternate write semantics
```

These are implementation questions, not assumptions.

## 6. Greenfield advantages

```text
Event-only Domain SoT can be foundational
single semantic write path can be foundational
security-plane separation can be foundational
LLM-facing schema can avoid compatibility aliases
rebuildability can be tested from the first implementation slice
legacy import/compatibility can be isolated
```

## 7. Greenfield risks

```text
duplicate implementation effort
migration/coexistence complexity
new operational failure modes
reimplementation of proven Broker features
risk of designing cleaner names without better behavior
```

## 8. Prototype decision gate

The decision remains OPEN until the experiment defined by `prototype-vertical-slice-v0.1.md` is executed.

Both candidates MUST implement the same fixed semantic chain:

```text
deterministic Observation
→ Claim
→ Fact projection
→ Problem
→ Plan
→ adversarial Review
→ Finding
→ Plan revision
→ stale revision rejection
→ Result
```

The candidates are:

```text
A. extend/refactor Memory Broker
B. greenfield Kawa Core beside Broker
```

No candidate may change the shared acceptance tests to favor its existing architecture.

## 9. Shared evidence record

Prototype evidence MUST be recorded using:

```text
prototype/comparison.schema.json
```

The record captures:

```text
persistent Domain changes
existing write/read paths modified
compatibility exceptions
migration steps
authority sources
Domain write primitives
rebuild test
stale-write test
scope isolation test
provenance spoof test
side-effect replay test
qualitative scores with evidence refs
rollback/replication/operator notes
independent review result
```

An unsupported score is invalid.

## 10. Hard-fail invariants

Either candidate loses immediately if it requires any of the following:

```text
unrebuildable authoritative current Domain state
caller-authored provenance/identity treated as attested
scope omission that widens visibility
silent stale overwrite of current Plan meaning
external side effects during Event replay
permanent multiple authoritative Domain write languages
permanent bidirectional dual authority during migration
```

Hard invariants dominate aggregate scoring.

## 11. Decision rule

```text
If only one candidate passes the hard invariants:
→ choose that architecture.

If both pass:
→ choose the lower combined structural, migration, and operational complexity.

If Memory Broker extension is materially simpler:
→ resolve F-006 toward extension/refactor.

If greenfield Kawa is materially simpler:
→ resolve F-006 toward greenfield Kawa + bounded migration.

If evidence is inconclusive:
→ keep F-006 open and refine the experiment; do not decide by preference.
```

## 12. Current state

```text
Current implementation direction: none selected as winner
Decision status: OPEN
Comparative prototype contract: fixed
Production replacement authorization: NONE
```

Greenfield Kawa may be implemented only as one prototype candidate. It is not presumptively the winner.

## 13. Independence requirement

The prototype author is not sufficient authority to close F-006.

Before closure, an independent reviewer MUST inspect both implementations, shared-test evidence, the comparison record, migration assumptions, and the proposed winner, with explicit instructions to refute the winner.

## 14. Migration/coexistence

If greenfield wins, the normative migration procedure is `memory-broker-migration-coexistence-v0.1.md`.

Key rules:

```text
one semantic family at a time
shadow reads before authority transfer
controlled dual observation only where side-effect safe
one authoritative writer after family cutover
all fleet Nodes verified
rollback proven before retirement
```

If extension wins, the same migration document still provides useful rollback/authority-transfer constraints for any in-place semantic conversion.

## 15. Reuse rule

Regardless of final architecture, Kawa SHOULD reuse proven Broker mechanisms and lessons where semantics remain compatible.

Examples:

```text
review practice
migration discipline
idempotency patterns
collector integrations
watcher/reconciliation lessons
epistemic evidence classifications
operational health checks
```

## 16. Core rule

> **Same slice. Same tests. Decide architecture by invariant cost and migration risk, not by naming preference.**
