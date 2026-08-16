# Kawa Scope Resolution v0.1

Status: Draft, normative security/interface contract

## 1. Purpose

Kawa minimizes LLM input, but automatic filling MUST NOT become automatic scope widening.

This document defines how Project/scope context is derived for reads, writes, search, work discovery, adapters, and Wizard guidance.

## 2. Core rule

> **Infer only when authority and context produce exactly one valid scope. Never interpret omission as all scopes.**

## 3. Resolution inputs

Scope MAY be derived from authority-bearing context such as:

```text
authenticated principal/workload
capability binding
current Work claim
explicit target reference
current Project-bound session context
resource binding
causation context
```

Caller-supplied natural-language text (unverified input) MUST NOT widen authorization scope.

## 4. Resolution algorithm

Conceptually:

```text
authenticate
→ determine authorized candidate scopes
→ apply explicit target/ref constraints
→ apply authority-bearing current Work/session context
→ candidate count
    1 → fill automatically
    >1 → needs_selection
    0 → reject / not_found according to disclosure policy
```

There is no fallback to global scope.

## 5. Explicit scope

A caller MAY provide a Project/scope only when the public operation meaningfully requires choosing among authorized scopes.

Providing a scope does not grant access to it.

Kawa still authorizes the requested scope independently.

## 6. Implicit scope

If scope is uniquely determined by authority-bearing context, the LLM-facing schema SHOULD omit the scope field entirely.

Example:

```text
current Work is bound to Plan P
Plan P belongs to Project A
authenticated Workload is authorized only for Project A
→ Project A is attached automatically
```

## 7. Ambiguity

If multiple authorized scopes remain plausible, Kawa MUST NOT guess.

Return:

```yaml
status: needs_selection
reason: scope_ambiguous
choices:
  - ref: kawa://project/...
  - ref: kawa://project/...
```

Only discoverable authorized choices may be shown.

## 8. Search

`kawa.search` with no explicit Project is legal only when authority-bearing context resolves one authorized search scope.

Otherwise it returns `needs_selection`.

Omission MUST NOT mean search across every Project visible to the principal unless a distinct explicitly authorized cross-project search operation is defined.

## 9. Emit

`kawa.emit` SHOULD derive Project context from the subject, current Work, causation, or authority-bearing session context whenever unambiguous.

The caller does not need to repeat `project` when it is already implied.

If the subject and requested Project disagree, Kawa rejects the request rather than silently reparenting the Event.

## 10. Bootstrap and work.next

Bootstrap and `work.next` may operate without explicit Project only when policy intentionally defines an authorized discovery scope.

Even then, authorization filtering occurs before candidate ranking or selection.

## 11. Disclosure oracle rule

A caller that cannot discover an object should normally receive `not_found`, regardless of whether the object exists outside its visibility.

`forbidden` is appropriate only when policy permits the caller to know the target exists but denies the requested action.

## 12. Cross-project operations

Cross-project operations are separate capabilities, not the accidental result of omitted scope.

Examples may include:

```text
project.search.cross_scope
situation.read.cross_scope
```

Such capabilities MUST be explicit and auditable.

## 13. Acceptance tests

```text
Omitting Project never widens to all Projects.
One uniquely implied Project is filled automatically.
Two plausible Projects produce needs_selection.
An explicit unauthorized Project does not grant visibility.
Unauthorized objects do not influence search ranking.
A hidden object cannot be enumerated through not_found/forbidden differences.
```

## 14. Core rule

> **Unique context may fill scope. Ambiguity must stop. Omission never means global.**
