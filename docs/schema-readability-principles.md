# Kawa Schema Readability Principles

Status: Normative design rule

## 1. Goal

A capable LLM should be able to inspect the schema and infer what Kawa is trying to represent without first reading an implementation manual.

> **The schema should explain the system.**

The database is not merely storage. Its names, types, relationships, constraints, and table boundaries are part of Kawa's machine-readable architecture.

## 2. Primary rule

Prefer names that describe domain meaning directly.

Good:

```text
events
event_problem
event_plan
event_observation
event_review
event_finding
event_approval
event_result
event_links
```

Avoid opaque or implementation-centric names such as:

```text
objects
records
items
entries
payloads
state_data
misc
attributes
extra
meta
```

A table or column whose purpose cannot be inferred from its name requires redesign unless the underlying concept is genuinely technical and internal.

## 3. Semantic names over abbreviations

Use complete semantic names in durable schema.

Prefer:

```text
problem_ref
review_ref
human_principal_ref
occurred_at
recorded_at
causation_id
resolution
```

Avoid:

```text
prb_id
rvw
usr
occ_ts
rec_ts
cause
res
```

Storage saved by abbreviations is insignificant compared with semantic ambiguity over a ten-year lifetime.

Opaque identifiers may use compact values internally, but column and relation names remain explicit.

## 4. Tables should correspond to concepts

A durable table should answer one obvious question.

```text
events             What happened, who/what did it concern, and when?
event_problem      What did this Event say about a Problem?
event_plan         What did this Event say about a Plan?
event_observation  What was observed?
event_review       What review activity occurred?
event_finding      What concern was raised or resolved?
event_approval     What approval was granted or revoked?
event_result       What result was recorded?
event_links        How is this Event semantically related to another reference?
```

Do not combine unrelated concepts merely to reduce table count.

Do not split one coherent concept merely to produce theoretically normalized but semantically obscure schema.

## 5. Columns should correspond to statements

A row should read approximately like a structured sentence.

Example:

```text
events.event_type        = problem.raised
events.subject_ref       = res_example_database
events.actor_ref         = wrk_example_agent
event_problem.problem_ref = prb_example
event_problem.statement  = replication is degraded
event_problem.rationale  = recent observations disagree with expected state
```

A model should be able to reconstruct the meaning without knowing PostgreSQL internals.

## 6. Relationships must use semantic verbs

`event_links.relation` is a deliberately small vocabulary of understandable relationships.

Prefer:

```text
supports
caused_by
addresses
reviews
resolves
supersedes
corrects
result_of
```

Avoid generic relations such as:

```text
related_to
associated_with
link
ref
other
```

unless no more precise stable meaning exists.

A generic relationship destroys information that would otherwise be available directly from the schema.

## 7. Null must have one clear meaning

Nullable fields MUST mean "not applicable or not supplied for this Event type" according to a documented schema rule.

Do not overload NULL to mean multiple states such as:

```text
unknown
not applicable
not yet computed
redacted
permission denied
conflicted
```

When those distinctions matter semantically, represent them explicitly.

## 8. Prefer constrained vocabulary

Stable categorical concepts SHOULD use explicit constrained values.

Examples:

```text
severity: low | medium | high | critical
verdict: pass | changes_required | blocked
outcome: succeeded | failed | partial | uncertain
```

Do not turn every string into a database enum prematurely; physical implementation may use CHECK constraints or reference tables. The important property is that the semantic vocabulary is finite and documented.

## 9. Do not encode meaning in positional conventions

Meaning MUST come from names and types, not from undocumented ordering or magic values.

Avoid designs where a model must know that:

```text
column_1 means subject
value 3 means high severity
negative number means unknown
empty string means revoked
```

The schema should say these things directly.

## 10. No generic JSON escape hatch

Kawa's durable Domain schema does not use generic JSONB payloads, `metadata`, `extra`, or `attributes` columns as semantic escape hatches.

If data matters, model it explicitly.

If it is large or domain-specific binary/structured material, reference an artifact.

If the schema cannot yet express it clearly, the schema is not ready to persist it as durable Domain meaning.

## 11. Public semantics and internal mechanics stay separate

The schema may contain internal operational tables such as:

```text
projection_checkpoints
work_leases
execution_ledger
```

Their names should also be self-describing, but they MUST remain visibly separate from Domain SoT.

A reader should not confuse:

```text
event_plan
```

with:

```text
current_plans
```

or:

```text
work_leases
```

Naming communicates authority and lifecycle.

Recommended prefixes:

```text
event_*    immutable Event payload components
current_*  rebuildable current projections
```

Operational tables use direct mechanical names rather than pretending to be Domain concepts.

## 12. Schema comments are secondary, not compensatory

PostgreSQL `COMMENT ON` statements SHOULD document non-obvious invariants, units, and constraints.

However, comments MUST NOT compensate for poor names.

Bad:

```sql
x text -- this is the problem statement
```

Good:

```sql
statement text
```

The name carries the primary meaning; the comment clarifies edge conditions.

## 13. LLM readability test

For every table or migration, ask a model with no Kawa-specific context:

```text
What does this table represent?
What does one row mean?
Which fields identify subject, actor, evidence, cause, and result?
Which data is authoritative and which is derived?
What relationships exist between these concepts?
```

If reasonable answers require a separate architecture explanation, schema readability should be improved.

## 14. Ten-year readability test

A schema change is acceptable only if a future maintainer or model can still answer:

```text
What happened?
What was observed?
What problem was identified?
What plan was proposed?
Why was it proposed?
What review challenged it?
What finding remained?
Who approved it?
What result occurred?
What caused what?
```

without reverse-engineering implementation conventions.

## 15. Design preference

When choosing between two schemas with comparable correctness and performance, Kawa prefers the one with lower semantic decoding cost for an LLM.

This does not mean verbose or redundant schema. It means explicit, orthogonal, unsurprising schema.

> **A good Kawa schema should read like a compact model of the world, not like a serialization format.**
