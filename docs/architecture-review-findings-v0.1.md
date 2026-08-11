# Kawa Architecture Review Findings v0.1

Status: Active review gate
Source: PR #1 adversarial architecture review + independent re-verification

## 1. Purpose

This document tracks architecture review Findings against the actual current tree.

A self-reported closure is not sufficient. A Finding closes only when the authoritative documents are consistent and the independent reviewer can re-verify the result.

Implementation MUST NOT begin while an unresolved CRITICAL/HIGH Finding remains or while the independent review gate is not satisfied.

## 2. First-review findings

### F-001 — Architecture process gate

Severity: CRITICAL
State: open pending re-verification pass

An independent re-verification has now occurred and correctly found additional inconsistencies after the first remediation round. That review therefore satisfies the independence requirement as a review event, but it did NOT pass the gate.

F-001 closes only when a subsequent independent re-verification of the corrected tree and F-006 prototype decision finds no unresolved CRITICAL/HIGH issue.

### F-002 — Security bootstrap and credential lifecycle

Severity: CRITICAL
State: remediation updated; requires independent re-verification

The canonical `security-model-v0.1.md` now agrees with the credential lifecycle contract:

```text
production revocation = MUST
production Workload authentication = proof-of-possession/channel bound
JWT string alone = insufficient
```

`identity-credential-lifecycle-v0.1.md` now selects one v0.1 production profile rather than presenting an implementation menu:

```text
single-use Human-issued enrollment token
TPM-backed Node key where available
Ed25519 / EdDSA
JWKS
short-lived JWT
cnf.jkt binding
DPoP-style proof-of-possession
```

### F-003 — Dangling semantic-schema reference

Severity: CRITICAL
State: resolved

Logical and physical schema versions are distinct and present.

### F-004 — Event-only SoT wording

Severity: HIGH
State: resolved

Current tables/projections are not a second SoT. Memory Broker is not Kawa's Domain SoT.

### F-005 — Migration/coexistence plan

Severity: HIGH
State: remediation updated; requires independent re-verification

`memory-broker-migration-coexistence-v0.1.md` now defines a concrete five-Node synthetic rollout procedure:

```text
per-Node inventory
per-Node shadow-read rollout
stop-on-first-node failure
family-specific acceptance gates
controlled dual observation
single-writer authority transfer
per-Node post-cutover verification
rollback procedure
family completion criteria
```

Production Node names/topology remain operator-private and are intentionally not published.

### F-006 — Extend vs greenfield

Severity: HIGH
State: OPEN comparative prototype gate
Tracking: GitHub issue #2 — `F-006: Run comparative prototype gate`

The independent reviewer correctly challenged an overstatement in supporting evidence: Memory Broker already has epistemic evidence typing such as observed/derived/inferred/intended/hypothesis.

That fact is explicitly acknowledged and MUST NOT be used to justify greenfield Kawa.

F-006 is now governed by:

```text
docs/prototype-vertical-slice-v0.1.md
prototype/comparison.schema.json
GitHub issue #2
```

The same fixed semantic slice must be evaluated as:

```text
A. Broker extension/refactor
B. greenfield Kawa Core
```

Both candidates use the same acceptance tests and hard-fail rules. F-006 cannot close through narrative argument or aggregate score alone.

Closure requires:

```text
both candidates implemented, unless one hard-fails early with reproducible evidence
shared tests executed
machine-readable comparison recorded
hard-fail rules applied
independent reviewer attempts to refute the proposed winner
final decision recorded with evidence
```

### F-007 — PlanStep incomplete/removal residue

Severity: HIGH
State: resolved in current tree; requires independent re-verification

Kawa v0.3 has no independent PlanStep entity.

The former `specification-v0.2.md` containing the stale `stp_017` HANDOFF example is now a Superseded redirect stub. Current `specification-v0.3.md` explicitly states that Work describes current objectives directly without a Step identity.

### F-008 — Documentation entry-point rot

Severity: HIGH
State: resolved in current tree; requires independent re-verification

README now begins its authority path with:

```text
supersession-matrix-v0.1.md
specification-v0.3.md
```

The Supersession Matrix itself is therefore reachable from the primary entry point.

### F-009 — Optional Project scope

Severity: HIGH
State: resolved at architecture-contract level

```text
one authorized scope → fill
multiple plausible scopes → needs_selection
none → reject/not_found
```

Omission never means global.

### F-010 — Revocation optional

Severity: HIGH
State: remediation updated; requires independent re-verification

The canonical Security Model now uses mandatory production language consistent with the Identity/Credential contract.

### F-011 — Stale-write mechanism

Severity: MEDIUM
State: remediation updated; requires independent re-verification

For every state-dependent write, Kawa MUST establish and retain one exact basis bound to the precise subject/action/Work context. Failure to recover the matching basis fails closed.

The earlier `MAY retain ... or return` ambiguity has been removed.

### F-012 — Superseded document authority

Severity: MEDIUM
State: resolved in current tree; requires independent re-verification

Superseded documents no longer remain as competing full specifications in the current tree. They are redirect stubs containing:

```text
Status: Superseded
Superseded by: <current document>
```

Full historical content remains in Git history.

Current redirect stubs include:

```text
specification-v0.2.md
core-logical-schema-v0.1.md
core-logical-schema-v0.2.md
event-taxonomy-v0.1.md
reducer-projection-contract-v0.1.md
postgresql-physical-schema-v0.1.md
postgresql-physical-schema-v0.2.md
mcp-contract-v0.1.md
wizard-error-guidance-v0.1.md
```

### F-013 — Approval materiality

Severity: MEDIUM
State: resolved at architecture-contract level

Approval binds exact required semantics; policy may add restrictions but cannot remove the mandatory high-risk binding set.

### F-014 — not_found vs forbidden oracle

Severity: MEDIUM
State: resolved at architecture-contract level

Hidden target existence is not disclosed through differentiated denial.

## 3. Independent re-verification findings

The independent follow-up review confirmed several earlier fixes and found remaining defects.

### R2-001 — Canonical Security Model contradicted mandatory revocation

Severity: HIGH
State: remediated; awaiting re-verification

Fixed in `security-model-v0.1.md`.

### R2-002 — Bootstrap/JWKS profile still presented as a menu

Severity: HIGH
State: remediated; awaiting re-verification

Fixed by selecting one v0.1 production profile in `identity-credential-lifecycle-v0.1.md`.

### R2-003 — Stale-write basis propagation was non-committal

Severity: MEDIUM
State: remediated; awaiting re-verification

State-dependent write basis propagation is now mandatory and exact-context-bound.

### R2-004 — Migration plan lacked per-Node procedure

Severity: HIGH
State: remediated; awaiting re-verification

Fixed by `memory-broker-migration-coexistence-v0.1.md`.

### R2-005 — Extend-vs-rewrite evidence overstated Broker deficiency

Severity: HIGH
State: accepted and reframed; decision remains open under F-006 comparative prototype

Broker epistemic evidence typing is acknowledged. Greenfield must win through prototype evidence, not by claiming the Broker lacks epistemic semantics.

### R2-006 — Supersession authority remained incomplete

Severity: HIGH
State: remediated; awaiting re-verification

README links the matrix first; old specifications are redirect stubs; Specification v0.3 is current.

### R2-007 — PlanStep residue remained in Specification v0.2

Severity: HIGH
State: remediated; awaiting re-verification

The stale Specification is now superseded and stubbed; v0.3 contains no PlanStep semantics.

### R2-008 — Claim field naming looked inconsistent across layers

Severity: MEDIUM
State: remediated; awaiting re-verification

The difference is now an explicit mapping between public semantic names and Event-envelope/storage names:

```text
Claim.ref      ↔ Event.event_id
Claim.subject  ↔ Event.subject_ref
Claim.claimant ↔ Event.actor_ref
```

Public semantics and storage vocabulary MUST NOT be mixed within one layer.

## 4. Current gate status

Open blocking work:

```text
F-006  execute comparative prototype gate (issue #2)
F-001  independent re-verification of corrected tree + F-006 decision
```

All other known CRITICAL/HIGH review defects are remediated in the current architecture tree but are not considered independently verified until the next review pass.

No production cutover is authorized.

## 5. Next independent-review attack list

The next reviewer should directly inspect the current tree and prototype evidence and attempt to falsify:

```text
canonical Security Model vs Identity/Credential consistency
old-document authority / stale examples
absence of PlanStep semantics in current docs
Claim public-to-storage mapping consistency
five-Node migration stop/rollback behavior
single-writer authority after family cutover
Broker epistemic typing treatment
prototype gate fairness between extension and greenfield
shared tests actually being identical for both candidates
comparison scores having concrete evidence refs
hard-fail rules not being bypassed by aggregate score
copied JWT replay against proof-of-possession requirement
scope omission and not_found/forbidden behavior
Approval target/operation substitution
concurrent stale-basis reuse
```

Any new CRITICAL/HIGH issue reopens the architecture gate.

## 6. Core rule

> **Review closure is something the next reviewer can reproduce, not something the author can declare.**
