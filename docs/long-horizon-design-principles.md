# Kawa Long-Horizon Design Principles

Status: Normative architectural principle

Kawa is intended to remain coherent and usable over a ten-year horizon. The design therefore optimizes for semantic stability, explicit boundaries, replaceable implementations, and low conceptual entropy rather than short-term convenience or current framework conventions.

## 1. Stable semantics over fashionable mechanisms

Public concepts MUST describe durable organizational ideas rather than vendor products, model families, orchestration frameworks, transport fashions, or implementation-specific terminology.

Preferred public vocabulary:

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

These concepts are expected to remain meaningful even if today's LLM providers, agent runtimes, MCP transports, databases, authentication protocols, and deployment stacks are replaced.

Implementation concepts such as lease records, cursors, revision counters, idempotency keys, tombstones, hashes, queues, vector indexes, or specific protocol tokens SHOULD remain internal unless an LLM must reason about their semantics directly.

## 2. One durable truth model

Kawa MUST preserve one unambiguous Source of Truth model:

```text
Events are durable.
Everything else is derived.
```

No convenience table, cache, projection, embedding store, brief, workflow state, or synchronization mechanism may silently become a second Source of Truth.

A derived structure MUST be disposable and rebuildable. If deleting it would destroy authoritative organizational history, the design boundary is wrong.

## 3. Semantic core, replaceable edges

The semantic core MUST remain independent from replaceable mechanisms.

```text
Stable core:
  Event semantics
  Project / Problem / Plan semantics
  causality
  provenance
  lifecycle
  conflict semantics
  authorization intent

Replaceable edges:
  PostgreSQL
  MCP
  vector search
  embedding models
  LLM providers
  CLI runtimes
  notification transports
  authentication implementations
  archive backends
```

A replacement of an edge technology SHOULD NOT require redefining domain meaning.

## 4. LLM-first means model-independent

LLM-friendly MUST NOT mean optimized for one current model.

A public Kawa interface SHOULD be understandable by a capable model with no Kawa-specific training and minimal standing instructions.

The target interaction remains:

```text
This project uses Kawa.
Connect to Kawa and continue the available work.
```

Tool names, schemas, returned state, references, and just-in-time guidance SHOULD provide the rest.

If an interface requires persistent prompt folklore, provider-specific prompt engineering, hidden conventions, or memorized protocol choreography, it is not sufficiently stable.

## 5. Explicit over clever

Prefer simple explicit structures over generalized abstractions whose value depends on implementation knowledge.

Prefer:

```text
Problem -> Plan -> Review -> Work -> Result
```

over generic workflow metamodels, arbitrary DAGs, universal entity systems, or extensible state-machine DSLs unless a demonstrated requirement justifies them.

Kawa SHOULD resist abstraction added only to make the implementation appear general.

## 6. Minimum public vocabulary

Every new public concept incurs long-term cognitive cost for future models and implementations.

Before introducing a public noun, verb, state, or tool, ask:

1. Would a capable new LLM naturally invent this concept when reasoning about the task?
2. Does the LLM need the concept to make a correct decision?
3. Is the concept likely to remain meaningful if the implementation changes completely?
4. Can Kawa enforce it internally instead?

If the answer to 2 or 3 is no, or 4 is yes, the concept SHOULD remain internal.

## 7. Few strong invariants

Kawa SHOULD prefer a small number of strong invariants over many local rules.

Core invariants include:

```text
One source of truth: Events.
One canonical domain write primitive: Emit.
Everything else is a projection.
Plans persist; agents do not.
Context is pulled, not pushed.
Hooks wake workers; Kawa holds the work.
Current state is the default; history is explicit.
Replicate events; rebuild understanding.
Event replay never repeats external side effects.
Conflicts are explicit; they are never silently overwritten.
```

New features SHOULD compose from these invariants rather than introduce exceptions.

## 8. Orthogonal concepts

Core concepts SHOULD have one primary meaning.

Examples:

- Event records that something happened.
- Observation records what was observed.
- Fact represents current interpreted truth.
- Problem represents something requiring explanation, decision, or action.
- Plan represents intended coordinated action.
- Review challenges a Plan.
- Work represents something an eligible worker can do now.
- Result records the outcome of action.

Avoid entities that simultaneously represent history, workflow state, authorization, assignment, and user interface concerns.

## 9. Separate semantics from mechanics

The LLM-facing interface speaks in semantic operations:

```text
raise a problem
revise a plan
review a plan
approve a plan
get work
complete work
record an observation
record a result
```

Kawa may internally implement these with optimistic concurrency, leases, cryptographic bindings, idempotency, tombstones, retries, cursors, or transactional state.

The public interface SHOULD expose those mechanics only when they materially change the decision the LLM must make.

## 10. Preserve causality and provenance

Ten-year usefulness depends more on explaining why state exists than on retaining every transient representation.

Kawa MUST preserve enough durable linkage to answer:

```text
What happened?
Who or what caused it?
What was observed?
What evidence supported the interpretation?
Why was this Plan chosen?
What authority permitted the action?
What result followed?
```

Derived summaries may change over time. The causal evidence chain must remain reconstructable.

## 11. Evolution by addition, not reinterpretation

Existing Event semantics MUST NOT silently change meaning across schema versions.

When semantics materially change, prefer:

- a new schema version,
- a new Event type,
- an explicit migration/projection rule,
- or a superseding interpretation.

Do not reinterpret old payloads according to new assumptions without explicit versioned logic.

A system ten years old must still be able to explain what an Event meant when it was recorded.

## 12. Backward readability before backward writability

Kawa SHOULD prioritize the ability of future versions to read and reconstruct old Events.

Old clients do not need indefinite permission to write every historic schema version. Future Kawa versions do need a deterministic path to interpret historic data.

## 13. Conservative dependencies

Core semantics MUST NOT depend on a specific external service being available forever.

Dependencies SHOULD be placed behind narrow adapters. A technology may be selected because it is currently practical, but its API model MUST NOT leak unnecessarily into Kawa's domain model.

Examples:

- MCP is a transport/interface choice, not a domain concept.
- PostgreSQL is a storage choice, not the definition of an Event.
- a vector database is a retrieval accelerator, not organizational memory.
- an LLM provider is a worker implementation, not an Actor ontology.

## 14. Boring data wins

Prefer conventional typed values, stable identifiers, explicit references, timestamps, enums, and flat structures.

Avoid opaque nested blobs when fields have durable semantics.

Use JSON only where the shape is genuinely variable or versioned payload polymorphism is necessary. Do not use JSON as a substitute for making schema decisions.

## 15. Human presentation is a projection

Human preference MUST NOT distort the machine-facing semantic model.

Human dashboards, reports, timelines, prose summaries, colors, grouping, and navigation are projections over Kawa state. They MAY evolve independently.

The Core is optimized for correctness, composability, machine interpretation, and continuity. Human interfaces adapt to the Core, not the reverse.

## 16. Failure is normal

Long-lived systems must assume:

```text
agents disappear
nodes disconnect
messages are lost
models change
projections corrupt
indexes are rebuilt
external calls time out
workers are replaced
schemas evolve
conflicts occur
```

These are normal operating conditions, not exceptional architectural failures.

The durable Event history and explicit reconciliation semantics must allow recovery without depending on conversation history or a particular worker instance.

## 17. No irreversible convenience

A short-term optimization SHOULD NOT create a permanent semantic commitment unless necessary.

Prefer designs that allow future replacement:

```text
projection can be rebuilt
index can be replaced
adapter can be rewritten
worker can be changed
transport can be changed
storage can be migrated
```

The harder something is to replace, the smaller and more stable its semantic surface must be.

## 18. Design test for every addition

Before accepting a feature into Core, ask:

```text
Will this concept still make sense in ten years?
Would a capable unfamiliar LLM understand it without special training?
Does it strengthen an existing invariant or create a new exception?
Can it be derived instead of stored?
Can it be implemented behind an adapter?
Can it survive a model/provider/database/transport replacement?
Does it preserve causality and provenance?
Does it introduce a second Source of Truth?
```

If the design performs poorly against these questions, it SHOULD remain outside Core.

## 19. Long-horizon architectural objective

Kawa should be small enough to understand, explicit enough to reconstruct, and semantic enough to outlive its implementation.

The intended property is not that the same code runs unchanged for ten years. The intended property is that the architecture can evolve for ten years without losing its identity.

> **Stable semantics. Replaceable mechanics. Durable events. Rebuildable understanding.**
