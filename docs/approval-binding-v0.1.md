# Kawa Approval Binding v0.1

Status: Draft, normative security contract

## 1. Purpose

Kawa Approval must not depend on an LLM deciding whether a Plan change is “material.”

This document defines deterministic approval binding and invalidation.

## 2. Core rule

> **Approval binds exact authority scope. Any mismatch makes the Approval inapplicable.**

The public model may say an Approval is `valid`, `stale`, `revoked`, or `expired`, but applicability is determined mechanically.

## 3. Mandatory approval binding

A high-risk Approval MUST bind at least:

```text
Plan identity
Plan semantic revision/fingerprint
resource / target scope
operation set
Human Principal / approving authority
expiry
```

Policy MAY add stricter bindings. Policy MUST NOT remove these minimum bindings for an operation that requires Kawa high-risk Approval.

Additional bindings SHOULD be included when applicable:

```text
security-sensitive constraints
required Review identity/state
requesting Workload
policy version
execution adapter / mediator class
```

The Agent does not choose which security fields are bound.

## 4. Semantic fingerprint

Kawa MAY internally compute a canonical semantic fingerprint over approval-bound Plan content.

The LLM does not create, compare, or submit this fingerprint.

Canonicalization MUST be deterministic.

Implementation mechanics may change, but equivalent bound semantics must produce equivalent applicability decisions under the same policy version.

The fingerprint MUST cover every Plan semantic value whose change could alter the approved target, operation, safety constraint, rollback, verification, or execution meaning when policy declares that value relevant.

## 5. Invalidation

An Approval becomes stale/inapplicable when any bound value changes.

Examples:

```text
target Resource changed
operation set expanded or otherwise changed
Plan semantic fingerprint changed
rollback removed when rollback was bound
security constraint weakened
review binding changed
requesting Workload changed where bound
policy requires re-approval after a new Finding
```

No LLM judgment of “materiality” is required.

A narrower operation is not automatically allowed under an Approval for a different operation set; operation-set applicability is defined by the binding policy, not textual intuition.

## 6. Changes that may remain applicable

A change may leave Approval applicable only when it changes no approval-bound semantic value.

Examples may include purely presentational data such as formatting or regenerated summary text, if policy explicitly excludes it from the semantic fingerprint.

This distinction is defined by canonical binding policy, not free-form interpretation.

## 7. Approval lifecycle

```text
pending
→ granted
→ valid
→ stale | revoked | expired
```

`stale` is derived when current bound semantics no longer match the granted proof.

`revoked` is an explicit authorization decision.

`expired` is derived from time.

## 8. Execution gate

Before a high-risk operation:

```text
authenticate requester
→ authorize capability/resource/operation
→ load current Plan semantics
→ compute mandatory + policy-added approval binding
→ verify approval proof
→ confirm target and operation match
→ confirm not revoked
→ confirm not expired
→ execute through the attested mediator
```

The check occurs immediately before the protected operation or at the closest attested execution boundary.

## 9. TOCTOU resistance

The execution adapter MUST NOT authorize against one Plan state and execute a different state silently.

The adapter or authorization layer must bind execution to the same approved semantic revision/fingerprint, target scope, and operation set used by the gate.

If any required binding changes between authorization and execution, execution fails with semantic conflict such as:

```text
approval_stale
plan_changed
scope_changed
operation_changed
```

## 10. Agent-facing minimization

The Agent does not supply:

```text
approval hash
approval revision
Human identity proof
policy version
security fingerprint
approval-valid boolean
mandatory binding selection
```

Kawa derives and verifies these values internally.

The Agent may only request the semantic action it intends to perform.

## 11. Approval is not Git review

A GitHub PR review or merge approval is not automatically Kawa Approval.

It may satisfy a Kawa approval requirement only when policy explicitly defines the GitHub authority and exact bound artifact/revision as an accepted approval mechanism, while still satisfying the mandatory Kawa approval binding set.

## 12. Acceptance tests

```text
Changing an approved target makes Approval stale.
Changing the approved operation set makes Approval stale or inapplicable.
A policy cannot omit Plan fingerprint, target, operation set, Human authority, or expiry from high-risk Approval.
Reformatting excluded presentation text does not invalidate Approval.
An LLM cannot mark its own Approval valid.
Execution cannot proceed if Plan changes after approval check.
Execution cannot substitute a different Resource after approval check.
A revoked Approval cannot become valid through replay or offline stale state.
```

## 13. Core rule

> **Do not ask whether a change is material. Bind what is authorized, then compare exactly.**
