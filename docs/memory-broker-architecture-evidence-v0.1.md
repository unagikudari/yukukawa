# Memory Broker Architecture Evidence v0.1

Status: Architecture evidence for F-005 / F-006
Scope: Evidence-based comparison against the current `unagikudari/memory-broker` implementation.

## 1. Purpose

Kawa MUST NOT justify a greenfield rewrite merely because its vocabulary is cleaner.

The decision must be based on architectural invariants and migration risk observed in the existing Memory Broker implementation.

## 2. Evidence observed in Memory Broker

Recent Memory Broker implementation history demonstrates that it already contains several mechanisms conceptually close to Kawa:

```text
versioned plans and review flows
approved-plan references
adversarial review gates
append-only activation history
deterministic watcher/cursor behavior
idempotent deduplication
current-vs-historical recall policy
node/fleet replication controls
semantic routing to deterministic SoT surfaces
```

This means Kawa is NOT justified merely by introducing Plan, Review, Observation, Work, or append-only history as names.

## 3. Evidence that extension has significant structural cost

The same implementation history shows constraints that are directly relevant to Kawa's invariants.

### 3.1 Generic memory substrate remains load-bearing

Broker semantics continue to pass through a generic memory surface using combinations such as:

```text
kind
class
tags
content
status
```

Recent implementation work explicitly documents tag-to-column promotion and class/catalog behavior affecting persisted semantics.

Kawa's intended invariant is different:

```text
stable typed Event family
+ explicit semantic links
+ rebuildable projections
```

Replacing the generic memory substrate in place would affect many existing read, write, recall, watcher, replication, migration, and compatibility paths simultaneously.

### 3.2 Existing semantics are distributed across many paths

Recent Broker changes identify multiple independently implemented recall lanes, including HTTP, MCP, briefing, semantic search, and raw SQL paths. Correctness changes have required explicit parity tests across all lanes.

Kawa instead requires one semantic contract whose transport/read implementations are adapters over the same projection model.

### 3.3 Fleet replication constraints shape schema design

Memory Broker currently operates with a multi-node replication topology in which publication membership, per-node/canonical classification, migration ordering, and replica triggers materially constrain changes.

Recent fixes document:

```text
publication classification drift
schema-before-publication ordering hazards
replica dedup behavior causing row divergence
canonical-only tables introduced to escape multi-writer shared fate
```

Kawa's federation invariant is intentionally different:

> Replicate Events. Rebuild understanding.

Adopting that invariant inside Broker would require changing the replication model beneath existing production behavior, not merely adding new tables.

### 3.4 Identity/provenance is not yet uniformly infrastructure-attached

Recent Broker work fixed a review verdict path where `source_agent_id` would otherwise have defaulted to a watcher identity instead of the actual reviewer.

This demonstrates that provenance is still partly write-path-specific.

Kawa requires:

> Identity is attached, never declared.

Trusted Node/Workload identity must be attached by the authenticated runtime uniformly before Domain semantics are processed.

### 3.5 Existing implementation contains compatibility/dead-path burden

Recent Broker work found paths that were structurally dead or behaviorally different from their apparent intent, including duplicate/legacy response paths and independently reimplemented briefing/read lanes.

This is normal for an evolved production system, but it increases the risk of changing its foundational SoT and write semantics in place.

## 4. Evidence in favor of reuse

The review's relabeling concern remains valid because substantial Broker machinery is reusable or conceptually proven:

```text
adversarial review practice
migration discipline
idempotency patterns
deterministic watchers
fleet reconciliation lessons
semantic routing lessons
operational health checks
existing migration source data
```

Kawa SHOULD reuse these lessons and, where practical, adapters or isolated implementation components.

Kawa SHOULD NOT duplicate mature Broker behavior merely to obtain different names.

## 5. Extend vs greenfield conclusion

The current evidence supports the following decision:

> **Keep Kawa as a greenfield Domain core, but treat Memory Broker as a first-class migration source, compatibility boundary, and source of proven operational mechanisms.**

Reason:

The largest Kawa differences are not entity names. They are foundational invariants that cut across Broker's existing production architecture:

```text
Event-only Domain SoT
fully typed authoritative Event payloads
one canonical Domain write primitive
infrastructure-attached identity/provenance
scope-before-retrieval authorization
replicate Events / rebuild projections
no generic memory/document substrate in the Domain core
```

Implementing all of these inside Memory Broker would amount to replacing its foundational write, read, storage, identity, and replication contracts while keeping the old runtime around them. That has a larger blast radius than introducing a small greenfield core beside Broker and migrating incrementally.

This conclusion is architectural, not a mandate for immediate cutover.

## 6. Coexistence strategy

Migration MUST be incremental:

```text
1. Inventory
   Identify Broker surfaces and data classes that map to Kawa semantics.

2. Import
   Convert selected durable Broker records to typed Kawa Events with explicit provenance.

3. Shadow read
   Compare Kawa projections against Broker answers without changing production decisions.

4. Controlled dual observation
   Deterministic collectors may feed both systems where replay/duplication is safe.

5. Semantic equivalence gates
   Compare current Problem/Plan/Observation/Fact/Review/Result interpretations.

6. Bounded write adoption
   Move one semantic family at a time to Kawa as authoritative producer.

7. Rollback
   Until a family is cut over, Broker remains available as the operational fallback.

8. Cutover
   Only after projection equivalence, security, recovery, and performance gates pass.

9. Retirement
   Remove legacy paths only after an explicit observation period and rollback window.
```

## 7. What MUST NOT happen

```text
big-bang migration
rewriting Broker history into apparently native Kawa history without provenance
making both systems authoritative for the same semantic family indefinitely
replaying imported Events as external side effects
silently translating ambiguous Broker rows into asserted Facts
```

Ambiguous legacy material should remain imported evidence with explicit provenance and uncertainty.

## 8. Decision rule

If a later prototype demonstrates that Memory Broker can satisfy the Kawa invariants with smaller risk and less complexity than the greenfield core, F-006 MUST be reopened.

Kawa is not entitled to remain greenfield by design preference alone.

## 9. Core rule

> **Reuse proven mechanisms. Do not inherit incompatible invariants.**
