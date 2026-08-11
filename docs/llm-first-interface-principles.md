# Kawa LLM-First Interface Principles

Status: Normative design rule

Kawa is machine-facing first. The primary consumer of the public interface is an LLM/Agent, not a human operator and not an implementation engineer.

Human aesthetic preference is not a design criterion for the public interface. Human administration, audit, and approval remain supported, but the interface exposed to reasoning agents MUST optimize for natural model comprehension and correct action selection.

## 1. Primary rule

> Expose concepts an LLM would naturally think of. Hide concepts that exist only to keep the system correct.

A concept belongs in the public LLM-facing vocabulary only when a capable general-purpose model can infer its meaning and likely use from the name and local context without requiring Kawa-specific training material.

If a concept requires standing instructions, glossary memorization, or framework-specific explanation before an LLM can use it correctly, Kawa SHOULD hide it behind a simpler public operation.

## 2. Public vocabulary

Kawa SHOULD keep the normal reasoning vocabulary small:

```text
Project
Problem
Plan
Observation
Fact
Review
Finding
Approval
Work
Result
```

These terms map directly to ordinary reasoning:

```text
What are we working on?      -> Project
What is wrong or unresolved? -> Problem
What should we do?           -> Plan
What was seen?               -> Observation
What currently appears true? -> Fact
What could be wrong with it? -> Review / Finding
May we proceed?              -> Approval
What should I do next?       -> Work
What happened?               -> Result
```

Domain-specific concepts such as Incident, Mission, Operation, Campaign, Case, or Business Initiative belong in domain schemas/projections rather than expanding the Core vocabulary.

## 3. Internal vocabulary

The following are normally implementation concerns and SHOULD NOT be required knowledge for an LLM using Kawa:

```text
revision sequence
expected_revision
optimistic lock
lease
lease TTL
heartbeat
projection cursor
tombstone
local sequence
idempotency key
scope hash
operation-set hash
conflict marker
materialized projection
reconciliation token
```

These concepts MAY appear in diagnostics, audit, administration, or low-level protocol traces, but they are not part of the ordinary reasoning contract.

Example:

```text
LLM action: revise this plan
Kawa internal: compare current revision, apply optimistic concurrency, invalidate stale review/approval bindings
LLM result: updated | conflict
```

The model does not need to supply `expected_revision` unless an exceptional low-level API explicitly requires it.

## 4. Natural verbs

Public operations SHOULD use verbs that an LLM would naturally choose:

```text
observe
raise_problem
reframe_problem
resolve_problem
propose_plan
revise_plan
review_plan
resolve_finding
approve_plan
next_work
claim_work
complete_work
record_result
search
get
```

Avoid exposing mechanical verbs such as:

```text
acquire_lease
advance_cursor
write_tombstone
compare_and_swap_revision
rebuild_projection
bind_scope_hash
```

unless the caller is explicitly an administrative/internal component.

## 5. Prefer state over protocol memory

An LLM SHOULD be able to reconnect with no conversation history and infer what to do from current Kawa state.

```text
connect
-> bootstrap
-> current Project / Problems / Plans / Findings / Work
-> next action
```

The model SHOULD NOT need to remember:

- previous MCP calls,
- a lease protocol,
- which revision number it last saw,
- hidden state-machine transitions,
- a long Kawa instruction document.

Kawa owns those mechanics.

## 6. Errors are guidance

Errors visible to an LLM SHOULD describe the semantic problem and the next valid action, not implementation mechanics.

Prefer:

```json
{
  "status": "conflict",
  "message": "The plan changed since you reviewed it.",
  "next_allowed_actions": ["get_plan", "review_plan"]
}
```

over:

```json
{
  "error": "expected_revision mismatch: expected=4 actual=5"
}
```

Prefer:

```json
{
  "status": "approval_required",
  "message": "This plan requires human approval before execution.",
  "next_allowed_actions": ["request_approval"]
}
```

rather than exposing approval hash mechanics.

## 7. Progressive disclosure

Responses SHOULD contain only the information needed for the current reasoning step.

```text
summary
important state
open problems/findings
next allowed actions
references for detail
```

Detailed provenance, history, schema, security metadata, and low-level coordination state are pulled through references when needed.

Context is pulled, not pushed.

## 8. Names beat documentation

Field, event, operation, and status names SHOULD be self-explanatory enough that a model can infer behavior from examples.

Prefer:

```text
problem.statement
plan.objective
review.verdict
finding.severity
work.status
result.outcome
```

Avoid unexplained abbreviations, framework jargon, and overloaded generic fields.

## 9. One obvious path

For common actions there SHOULD be one obvious public way to perform them.

```text
Need to change a Plan? -> revise_plan
Need work?             -> next_work
Saw something?         -> observe
Need history?          -> search(history=true)
```

Multiple equivalent APIs increase model uncertainty and SHOULD be avoided.

Convenience APIs MAY internally reduce to the canonical Event write path.

## 10. LLM-friendliness test

Before exposing a new concept or field, ask:

1. Would a capable LLM naturally invent this concept while solving the task?
2. Can it infer the intended operation from the name alone?
3. Does exposing it improve reasoning, or only reveal implementation machinery?
4. Can Kawa derive or manage it internally instead?
5. Would removing it reduce prompt/context requirements?

If the answer to (1) or (2) is no and (4) is yes, the concept SHOULD remain internal.

## 11. Security boundary

LLM-friendly does not mean permissive.

Kawa may hide authentication, revision, lease, secret, and approval mechanics from the model while enforcing them more strictly in infrastructure.

> The LLM sees simple semantics. Kawa enforces precise mechanics.

The model remains an authenticated but untrusted decision-maker.

## 12. Design target

A new capable model with access to Kawa tools and current project state SHOULD be able to perform useful work without reading a Kawa manual first.

The ideal bootstrap instruction is approximately:

```text
This project uses Kawa. Connect to Kawa and continue the available work.
```

Everything else should be discoverable from tool names, schemas, current state, and just-in-time guidance.
