# Kawa LLM Write Input Minimization v0.1

Status: Draft, normative
Scope: Minimize caller-supplied fields for Agent/Skill writes while preserving Event-only SoT, trusted provenance, and deterministic security metadata.

## 1. Core rule

An Agent or Skill should supply only semantic values that cannot already be determined by Kawa.

> **If Kawa can determine a value safely and unambiguously, the caller should not supply it.**

The goal is not merely optional fields. The goal is to remove unnecessary choices from the LLM-facing interface.

```text
Semantic intent from caller
+ trusted runtime context
+ current Kawa state
+ schema
+ policy
= complete durable Event
```

## 2. Caller-controlled versus system-controlled fields

Caller-controlled fields are limited to semantic content that requires reasoning, judgment, or explicit selection.

Examples:

```text
problem statement
plan objective
plan rationale
finding statement
finding severity
resolution rationale
selected target when multiple targets are valid
explicit evidence reference when it cannot be inferred
```

System-controlled fields include values that are derived from trusted context or protocol state.

Examples:

```text
actor_ref
observer_ref
node_ref
workload_ref
recorded_at
local_sequence
schema_version
correlation_id
causation_id
project_ref when uniquely determined by current context
subject_ref when uniquely determined by current operation/work item
observation_method for trusted collectors
approval/security bindings
revision/concurrency metadata
```

These system-controlled values SHOULD NOT appear as caller-writable fields in normal LLM-facing schemas.

## 3. Identity is never caller input

The caller MUST NOT provide authoritative identity fields.

```text
actor_ref
observer_ref
node_ref
workload_ref
human_principal_ref
```

are attached by trusted infrastructure.

A caller-provided identity-like value is at most untrusted content and MUST NOT override authenticated identity.

> **Identity is attached, never declared.**

## 4. Provenance is never free text

Trusted provenance is established by the execution path, not by text supplied by the Agent.

For deterministic Observation ingestion:

```text
observer_ref        <- authenticated workload
observation_method  <- trusted Collector/Adapter
predicate           <- normalized collector mapping
value               <- deterministic tool output
```

The LLM or Skill may request an observation, but it MUST NOT be able to claim that the value came from `ansible.setup`, `scanner.nessus`, `system.hostname`, or another trusted method unless that method actually executed.

> **Provenance is established by execution, never claimed by text.**

## 5. Context should fill references

When a write occurs while the caller is already operating inside a known Project, Plan, Review, Finding, or Work context, Kawa SHOULD fill those references automatically.

Example:

```text
Current Work:
  plan = kawa://plan/pln_example
  project = kawa://project/prj_example
```

A Plan revision action SHOULD NOT require the caller to repeat both refs if the current Work uniquely identifies them.

Bad:

```yaml
project: kawa://project/prj_example
plan: kawa://plan/pln_example
actor_ref: wrk_agent
schema_version: 2
rationale: Fix review finding.
```

Preferred:

```yaml
rationale: Fix review finding.
```

Kawa fills the rest from trusted context.

## 6. Auto-fill only when unambiguous

Kawa MUST NOT guess.

Auto-fill is allowed only when the value is uniquely determined by:

```text
authenticated identity
current Work
current object context
trusted Adapter
schema
policy
request causality
```

If multiple values remain valid and the distinction is semantically meaningful, Kawa returns `needs_selection` or `needs_input`.

Example:

```yaml
status: needs_selection
reason: multiple_possible_targets
choices:
  - kawa://resource/res_example_a
  - kawa://resource/res_example_b
```

This preserves minimal input without introducing hidden inference.

## 7. Time semantics

`recorded_at` is always system-managed.

`occurred_at` is system-managed when the event occurred as part of the current trusted execution path.

The caller supplies an occurrence time only when recording a historical/external occurrence whose time cannot be derived safely.

For deterministic collectors, the collector/runtime establishes observation time.

The LLM should not normally set timestamps manually.

## 8. Event type may also be inferred by a Skill

`kawa.emit` remains the canonical write primitive internally, but a Skill or semantic convenience action MAY deterministically choose the Event type.

Examples:

```text
raise problem
  -> problem.raised

revise current plan
  -> plan.revised

record review finding
  -> finding.raised

complete review
  -> review.completed
```

The LLM need not spell the Event type when the invoked Skill already has exactly one valid semantic meaning.

The Skill MUST compile to the canonical Event schema and MUST NOT create a second write model.

## 9. Subject may be inferred

The LLM should not repeat `subject_ref` when the current operation already identifies the subject uniquely.

Example:

```text
Current context = Plan pln_example
Action = revise
```

Required input may be only:

```yaml
rationale: Address the unresolved security finding.
```

Kawa derives:

```text
event_type  = plan.revised
subject_ref = kawa://plan/pln_example
project_ref = containing project
actor_ref   = authenticated workload
causation   = current Work/request
```

## 10. Evidence should be inferred when causal context is exact

When an action is directly caused by a known Finding, Observation, Result, or prior Event, Kawa SHOULD attach the semantic link automatically if the causal relationship is unambiguous.

Example:

```text
Work = resolve finding fnd_example by revising plan pln_example
```

The Plan revision may automatically receive:

```text
based_on -> kawa://finding/fnd_example
```

The caller supplies extra evidence only when choosing or adding evidence is itself a semantic decision.

## 11. Fact is not directly rewritten

Fact remains a Projection.

An Agent MUST NOT directly mutate a current Fact table or write `fact.value = ...` as authoritative state.

Instead, the Agent contributes durable semantic evidence/assertion through an appropriate Event path, and reducers reconstruct Fact.

Examples:

```text
deterministic tool output
  -> observation.recorded
  -> Fact projection

verification outcome
  -> result.recorded
  -> Fact projection

Agent interpretation
  -> accountable semantic Event/proposal with provenance
  -> policy decides whether/how it influences Fact
```

A convenience Skill may look like "record this fact" to the Agent, but internally it MUST preserve the distinction between assertion/evidence and derived Fact.

## 12. Rewrites are semantic events, not object mutation

A Skill that appears to "rewrite" a Problem, Plan, Finding, or other concept should emit the canonical semantic Event.

Examples:

```text
rewrite problem framing -> problem.reframed
rewrite plan            -> plan.revised
resolve finding         -> finding.resolved
```

The Skill should ask only for the semantic delta that cannot be inferred.

## 13. Three classes of fields

Every public write field SHOULD be classified as exactly one of:

```text
1. required_semantic_input
   The caller must decide it.

2. inferred_context
   Kawa derives it from current semantic context.

3. trusted_metadata
   Kawa derives it from authenticated/runtime infrastructure.
```

A field that does not clearly fit one class is a design smell.

## 14. Default public write shape

The ideal Skill-facing write is small.

Problem example:

```yaml
statement: Replication is degraded.
```

Plan revision example:

```yaml
rationale: Address the high-severity review finding.
```

Finding example:

```yaml
severity: high
statement: Rollback does not cover partial migration failure.
```

Deterministic observation request example:

```yaml
collect: system.hostname
```

Everything else should be derived when possible.

## 15. Security consequence

Input minimization is also a security control.

Removing trusted fields from caller control prevents spoofing of:

```text
identity
provenance
node/workload origin
approval context
schema version
causality
trusted timestamps
```

The LLM has less authority because it has fewer security-relevant knobs.

## 16. LLM-friendly consequence

The interface should minimize semantic decoding and form-filling burden.

The Agent should think:

```text
What do I mean?
```

not:

```text
Which metadata, identity, timestamp, revision, project, node, schema version, and correlation fields do I need to copy correctly?
```

> **Require intent. Infer context. Attach trust.**

## 17. Acceptance tests

A conforming implementation should pass tests equivalent to:

```text
An Agent cannot set its authoritative actor_ref.
An Agent cannot set observer_ref for a trusted collector.
An Agent cannot claim observation_method without executing that method.
A Plan revision in a unique current Plan context does not require project/plan refs.
A write in a unique Work context inherits causation automatically.
recorded_at/local_sequence/schema_version are never required from the Agent.
Ambiguous target/context returns needs_selection instead of guessing.
A convenience Skill compiles to canonical Events rather than mutating projections.
Fact cannot be directly overwritten by an Agent.
```

## 18. Core rule

> **Require intent. Infer context. Attach trust.**
