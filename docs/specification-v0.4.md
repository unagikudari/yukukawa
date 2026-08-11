# Kawa Specification v0.4

Status: Draft, current consolidated architecture specification
Supersedes: `specification-v0.3.md`
Date: 2026-08-11

> ゆく川の流れは絶えずして、しかも元の水にあらず。

> **Actors pass through Kawa. Events remain. Understanding changes.**

## 0. Executive Summary

Kawa is an event-sourced continuity substrate for organizational Situation Awareness and the OODA loop.

Kawa is not an Agent memory product and is not a general-purpose workflow engine. Its purpose is to preserve organizational continuity when Humans, Agents, Nodes, runtimes, models, and tools are replaced, disconnected, or lost.

Kawa continuously reconstructs the best available current understanding from durable Events while keeping authority, provenance, execution, and derived state explicitly separated.

The three primary outcomes are:

```text
Resilient Organization
Continuous Situation Awareness
Faster, unbroken OODA Loop
```

Kawa is domain-neutral. The same Core semantics may be used for Cyber Defense, C2, Red Team, Incident Response, Engineering/Operations, and executive/business decision support. Domain-specific nouns belong in typed schemas, adapters, projections, or external systems rather than in the Core vocabulary.

Kawa's stable architectural rule is:

> **Stable semantics. Replaceable mechanics. Durable Events. Rebuildable understanding.**

## 1. Design Constitution

```text
One Domain Source of Truth: Events.
One canonical Domain write primitive: Emit.
Everything current is reconstructed.
Situation Awareness is composed, not stored.
Plans persist; Agents do not.
Agents coordinate through shared state, not conversation.
Context is pulled, not pushed.
Guidance is just in time.
Constraints belong in the system, not in prompts.
Default to current; history is explicit.
Derived state must be disposable.
Replicate Events. Rebuild understanding.
The event stream remembers. The working set must forget.
```

A design that violates one of these rules requires an explicit architecture revision; it is not an implementation detail.

## 2. Source of Truth and Reconstruction

Events are Kawa's only durable Domain Source of Truth.

```text
Events + Policy + Time
        ↓
Deterministic reducers
        ↓
Current Understanding
```

Current Project, Problem, Plan, Observation view, Claim view, Fact, Review, Finding, Work, Approval status, Brief, indexes, and caches are derived or interpreted state.

They may be implemented as SQL VIEWs, materialized views, reducer-maintained tables, caches, or indexes, but they MUST be disposable and reconstructable from durable inputs.

No semantic Last Write Wins is permitted for unresolved disagreement. Conflict is explicit current state.

Corrections do not rewrite history. They create later Events with explicit semantic links such as `corrects`, `supersedes`, or `resolves`.

## 3. OODA Mapping

```text
Observe
  Event / Observation

Orient
  Claim / Fact / Problem / History / Preflight

Decide
  Plan / Review / Finding / Approval

Act
  Authorized trusted execution / Result
        ↓
      new Events
        ↓
      Observe again
```

Kawa is therefore a continuity layer for organizational Situation Awareness and the OODA loop, not a replacement for the systems that actually execute domain actions.

## 4. Public Semantic Model

The preferred public concepts are:

```text
Project
Problem
Plan
Observation
Claim
Fact
Review
Finding
Approval
Work
Result
```

These names are chosen for direct semantic readability by capable LLMs and Humans.

Mechanics such as the following remain internal unless an operator/debug interface explicitly needs them:

```text
revision sequence
PlanRevision entity
lease
fencing token
CAS / expected revision
projection cursor
idempotency key
semantic fingerprint
approval hash
replication cursor
stale-write basis
security-plane credential internals
```

### 4.1 Entity identity rule

A concept receives an enduring independent identity only when it has an independently evolving lifecycle across multiple Events.

```text
Project  → enduring identity
Problem  → enduring identity
Plan     → enduring identity
Review   → enduring identity
Finding  → enduring identity

Observation → Event identity is sufficient
Claim       → Event identity is sufficient
Approval    → grant Event identity is sufficient by default
Result      → Event identity is sufficient by default
```

Test:

> **Does this concept need to live beyond one Event as an independently evolving thing?**

If no, do not manufacture another Domain ID.

## 5. Epistemic Model

Kawa separates measurement, assertion, and accepted current interpretation.

```text
Observation
= what a trusted observer/tool measured, received, or deterministically collected

Claim
= what an accountable Human/Agent asserts or infers

Fact
= what current evidence, authority policy, scope, and time presently accept as true
```

Therefore:

```text
Observation != Claim
Claim != Fact
Fact != Source of Truth
```

### 5.1 Observation

A deterministic Observation is created by a trusted Collector/Adapter execution path.

Conceptually:

```text
subject_ref          = what was observed
observer_ref         = authenticated Workload that observed it
observation_method   = trusted deterministic method/tool used
predicate            = property observed
value                = observed value
occurred_at           = when observation occurred
```

The caller may request that an observation be performed, but cannot author trusted provenance.

> **The caller requests an observation. The trusted collector creates the Observation.**

### 5.2 Claim

An LLM/Human inference enters Kawa as `claim.recorded`, not as a deterministic Observation and not as a direct Fact rewrite.

A Claim may be based on Observations, prior Claims, Results, Problems, external artifacts, or other Events.

Self-reported confidence is assertion content, not trusted authority.

A later changed assertion is a new Claim Event linked to the previous Event. Historical Claims are never edited.

### 5.3 Fact

Fact is a deterministic projection/interpretation over applicable evidence and policy.

Possible Fact states include:

```text
clear
conflicted
unknown
```

Unknown, false, null, not-applicable, and conflicted are not interchangeable.

There is no ordinary `write_fact` or `rewrite_fact` operation.

## 6. Stable Event Taxonomy

The current stable Domain families are:

```text
project.*
problem.*
plan.*
observation.*
claim.*
review.*
finding.*
approval.*
result.*
```

Current stable Event set:

```text
project.created
project.updated
project.ended

problem.raised
problem.reframed
problem.resolved

plan.proposed
plan.revised
plan.started
plan.ended

observation.recorded
observation.corrected

claim.recorded

review.started
review.completed

finding.raised
finding.resolved

approval.granted
approval.revoked

result.recorded
```

Derived states such as `plan.ready`, `review.stale`, `approval.valid`, `fact.current`, `work.ready`, `lease.expired`, and `projection.updated` are not Domain Events.

Stable relation vocabulary currently includes:

```text
addresses
based_on
supports
reviews
corrects
resolves
supersedes
result_of
revokes
```

Avoid generic relations such as `related_to`, `misc`, or `associated_with`.

## 7. Event Envelope and Physical Persistence

Every Event has one semantic subject: the thing the Event is about.

Canonical envelope semantics:

```text
event_id
event_type
subject_ref
actor_ref?
observer_ref?
project_ref?
origin_node
workload_ref
occurred_at
recorded_at
origin_seq
correlation_id?
causation_id?
source_message_id?
schema_version
```

Relationships live in typed semantic links rather than duplicate family IDs.

`origin_node` / `origin_seq` are the **immutable, node-independent** origin coordinates governed by `event-log-and-replication-v0.1.md` (#25): `(origin_node, origin_seq)` is the replication cursor key, stable across replication / rebuild / compaction / observation on any node — never a local storage position. The replication frontier is **per-origin**, not a scalar `origin_seq`.

The durable physical Core consists conceptually of:

```text
events
event_links
typed event-family payload tables
```

There is no authoritative generic Domain `jsonb`, `metadata`, `extra`, or arbitrary attribute escape hatch.

PostgreSQL is the current implementation target, not part of the durable semantic contract.

## 8. Problem and Plan

### 8.1 Problem

A Problem is an organizational interpretation that something requires explanation, decision, or action.

A Problem is not objective truth. Its statement and rationale remain distinct from its evidence.

Reframing the same conceptual issue keeps the Problem identity; a genuinely different issue receives a new Problem.

### 8.2 Plan

A Plan is the current intended course of action addressing one or more Problems.

A Plan survives the Agent that authored or executes it.

Kawa Core has no independent `PlanStep` entity. Plan structure may be represented by:

```text
small typed Plan semantics
Git-managed artifact
external domain-specific workflow/resource
```

Work describes currently eligible objectives directly and need not expose Step mechanics.

Plan readiness is derived from current semantic state, applicable Review/Finding state, Approval state, evidence validity, and policy. There is no `plan.ready` Event.

## 9. Review, Finding, and Approval

### 9.1 Adversarial Review

Significant Plans SHOULD be challenged by an independent reasoning path, and high-risk Plans MUST be independently reviewed according to policy.

For independence-sensitive review:

```text
Author Workload != Reviewer Workload
```

Review attempts to falsify the Plan, including:

```text
root cause
insufficient/obsolete evidence
schema/dependency impact
abstraction leakage
historical failed analogues
security boundary failure
rollback/failure modes
verification weakness
scope expansion
```

High/critical unresolved Findings normally block Plan readiness/start.

### 9.2 Human Approval

Human login is not Human Approval.

High-risk Approval binds at least:

```text
Plan identity
Plan semantic revision/fingerprint
resource / target scope
operation set
Human approving authority
expiry
```

Policy may add additional restrictions but cannot remove this minimum high-risk binding set.

Approval applicability is deterministic. Do not ask an LLM whether a change is “material.” If any bound semantic value changes, the Approval becomes stale/inapplicable.

Execution MUST re-check the binding immediately before the protected operation or at the closest trusted execution boundary.

A GitHub PR approval is not automatically Kawa Approval. It may satisfy an Approval requirement only when policy explicitly binds the GitHub authority and exact artifact/revision.

## 10. Work and Mortal Agents

Kawa owns shared state; workers temporarily perform eligible Work.

```text
Plans persist; Agents do not.
Hooks wake workers. Kawa holds the work.
```

`work.next` is a Query over current semantic state, authorization, capability, policy, and internal coordination.

Agent-facing Work exposes semantic objectives, not lease TTLs, fencing tokens, heartbeat counters, or CAS state.

If a worker disappears, Work may become eligible again after trusted coordination determines that reassignment is safe.

Uncertain external side effects require reconciliation; worker loss must never imply that an external action did not already occur.

## 11. LLM-First Interface

Kawa is consistently LLM-first. Human aesthetic preference does not override semantic clarity to a capable model.

Core rules:

```text
Explicit semantics.
One obvious path.
Minimal interpretation.
Guided recovery.
```

The caller supplies only irreducible semantic intent.

> **Require intent. Infer context. Attach trust.**

If Kawa can determine a value safely and unambiguously, the caller should not supply it.

Values normally inferred/attached include:

```text
new opaque entity reference
Project/scope when unique
subject when uniquely implied by Work
actor/workload/node identity
observer identity for deterministic collectors
recorded time
schema version
causation/correlation
observation_method from trusted execution
stale-write basis
approval binding metadata
semantic links implied uniquely by Work/causation
```

If multiple valid semantic choices remain, Kawa returns `needs_selection` or `needs_input`; it does not guess.

## 12. MCP Core Contract

The current minimal semantic operation set is:

```text
kawa.bootstrap
kawa.get
kawa.search
kawa.emit
kawa.work.next
```

Durable meanings are:

```text
orient
read
search
emit
continue work
```

MCP itself is replaceable.

### 12.1 Emit

`kawa.emit` is the only canonical public Domain write primitive.

Event-specific input MUST be exposed through typed/discriminated schemas (`oneOf` or equivalent). `fields: arbitrary object` is non-conforming.

Convenience Skills/tools may use natural actions, but they MUST deterministically compile to the same Emit semantics and must not bypass authentication, authorization, scope, stale-write, approval, or Event-schema checks.

### 12.2 Wizard

Recoverable authenticated failures return deterministic semantic guidance.

Common outcomes include:

```text
ok
needs_input
needs_selection
conflict
blocked
approval_required
capability_required
precondition_failed
not_found
forbidden
retryable
unsafe
```

Wizard is deterministic, not an embedded LLM planner.

> **No valid authentication, no Wizard.**

## 13. Scope and Disclosure

Automatic input minimization MUST NOT become automatic authority widening.

Scope resolution order:

```text
authenticate
→ determine authorized candidate scopes
→ apply explicit target constraints
→ apply trusted Work/session context
→ exactly one scope: fill
→ multiple scopes: needs_selection
→ no authorized scope: reject/not_found
```

Omission never means global.

Cross-project operations require explicit capabilities.

Authorization/visibility filtering MUST occur before retrieval and semantic ranking.

Disclosure rule:

```text
caller cannot discover target
→ normally not_found

caller may know target exists but action is denied
→ forbidden
```

Hidden state must not become an authorization oracle through error differences.

## 14. Security Architecture

Kawa assumes an Agent may be authenticated and still be wrong, compromised, prompt-injected, or malicious.

LLM goodness is not a security boundary.

Security invariants:

```text
Authenticate before semantic processing.
Authorize before disclosure.
Identity is infrastructure-attested, not Agent-asserted.
A Resource handle is not authority.
Capabilities are explicit and scoped.
Secrets are mediated, not disclosed.
Human Approval is cryptographic and exact-scope-bound.
Event replay never repeats external side effects.
Prompt text cannot grant authority.
Derived state cannot become authority by accident.
```

### 14.1 Identity layers

```text
Human Identity
Node Identity
Workload Identity
Logical Agent Identity
```

Workload Identity is the primary NHI authorization boundary. Logical Agent identity is descriptive/accountability metadata and cannot replace Workload Identity as the authorization root.

### 14.2 v0.1 production credential profile

The current production profile selects one obvious path rather than a menu:

```text
Human-authorized single-use enrollment token
TPM-backed Node key where supported
Ed25519 / EdDSA signing profile
JWKS public-key distribution
short-lived JWT Workload credential
cnf.jkt key binding
DPoP-style proof-of-possession (or equivalent bound runtime proof)
mandatory revocation
```

A copied JWT string alone is insufficient production Workload authentication.

Revocation is mandatory for Node identity, Workload identity, capability bindings, Human Approval/break-glass authority, and signing keys.

Offline authority is bounded by explicit staleness policy. High-risk mediated execution blocks when revocation/trust freshness is insufficient.

### 14.3 Secrets and Resources

Resource handles identify what is targeted; they do not confer authority.

Raw secrets should not be given to Agents. Trusted Adapters/Secret Brokers perform authorized operations using protected credentials internally.

Private keys, raw JWTs, secret mappings, resource-resolver mappings, live topology, revocation state, and exploitable operational weakness remain outside Domain Events and outside public repository examples.

## 15. Stale-Write Correctness

LLMs do not manage expected revisions, ETags, CAS values, or row versions.

Kawa internally maintains an opaque trusted `basis` for each state-dependent action.

Basis is bound to the exact applicable context, including as needed:

```text
subject
semantic action/event family
Project/scope
Work/Review/Finding context
semantic Event frontier
policy/schema identity
```

For every state-dependent write, Kawa MUST establish and recover the exact matching trusted basis. If it cannot, it fails closed.

A basis for Plan A cannot authorize Plan B. Concurrent Work in one authenticated session cannot exchange bases.

Independent Observations that do not depend on prior current Fact state are not rejected merely because an unrelated projection changed.

Public conflicts remain semantic:

```text
plan_changed
problem_reframed
review_stale
approval_stale
state_changed
```

## 16. External Side Effects

Kawa separates:

```text
Intent
→ Authorization
→ Trusted Execution
→ Observation / Result
```

An Agent saying “I executed X” is not proof that X occurred.

Event replay MUST NEVER invoke an external side effect.

Execution adapters require idempotency and reconciliation sufficient for:

```text
timeout
retry
duplicate request
success followed by Kawa write failure
unknown external outcome
partial failure
```

## 17. Git and GitHub Boundary

Kawa does not duplicate Git semantics.

```text
Plans in Kawa.
Artifacts in Git.
References between them.
```

Git remains the SoT for repositories, branches, commits, diffs, pull requests, and merge history.

Kawa remains the semantic authority for why work exists, Problem/Plan meaning, Review/Finding state, applicable Human Approval, organizational Result, and continuity.

GitHub may execute Git-native mechanics:

```text
repository/branch creation
PR lifecycle
review requests
required status checks
rulesets/branch protection
Actions
merge queue
webhooks
workflow_dispatch / equivalent triggers
```

Trusted Git Adapter bridges the two.

```text
Kawa says WHAT/WHY should happen.
GitHub handles HOW Git work progresses.
Adapter translates evidence and intent between them.
```

Important non-equivalences:

```text
GitHub PR approved != Kawa Review completed
GitHub PR approved != Kawa Human Approval
branch merged       != Plan completed
CI passed           != Plan safe
```

GitHub webhook input is deterministic external evidence and must enter through a trusted Adapter, not by allowing GitHub text to directly mutate Kawa authority.

## 18. Federation and Node Independence

Nodes may operate independently/offline subject to security policy.

Kawa federation principle:

> **Replicate Events. Rebuild understanding.**

Event IDs must not collide. Events are never overwritten. Semantic conflicts may exist and must be explicit.

Concurrent interpretations are not silently resolved with Last Write Wins.

A stale offline Node must not resurrect revoked authority or obsolete semantic state on reconnect.

High-frequency telemetry may remain in specialized external systems; Kawa stores meaningful observations, aggregates, thresholds, or references rather than becoming a telemetry database.

## 19. Performance Objectives

Initial local-node hot-path objectives:

```text
single Event emit                 p95 < 40 ms
current Project/Problem/Plan get p95 < 40 ms
work.next                         p95 < 40 ms
small current projection query   p95 < 40 ms
```

Historical semantic search, full projection rebuild, federation reconciliation, and archive recovery are outside this hot-path SLO.

Do not optimize hypothetical paths before measurement.

## 20. Memory Broker Boundary and Migration

Memory Broker is an operational legacy system, migration/coexistence source, and source of proven mechanisms. It is not automatically Kawa's architectural base.

The extend-vs-greenfield decision remains OPEN until implementation evidence resolves F-006.

Kawa MUST NOT justify greenfield merely by claiming that Memory Broker lacks concepts such as Plan, Review, Observation, or epistemic typing; Broker already contains significant related mechanisms and classifications.

Migration rule:

> **One semantic family, one authority at a time. Observe twice if safe; decide once.**

Default migration sequence:

```text
1. deterministic Observation
2. historical Result/evidence references
3. Claim/inferred legacy evidence
4. Problem
5. Plan
6. Review/Finding
7. approval/execution-adjacent integration after security gates
```

Per-family migration requires:

```text
per-Node inventory
mapping freeze
idempotent import
per-Node shadow-read rollout
stop-on-first-node failure
controlled dual observation only where side-effect safe
single-writer authority transfer
per-Node post-cutover verification
rollback window
family completion gate
```

There is no indefinite bidirectional dual authority.

Fact is reconstructed; it is not migrated as an authoritative current row.

## 21. Comparative Prototype Gate (F-006)

Architecture is decided by evidence, not preference.

Both candidates implement the same fixed semantic slice:

```text
Candidate A: extend/refactor Memory Broker
Candidate B: greenfield Kawa Core
```

Fixed slice:

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

Both candidates MUST use the same acceptance-test revision and comparison schema.

Hard-fail conditions include:

```text
current Domain state cannot be rebuilt from durable inputs
caller can self-assert trusted identity/provenance
scope omission can widen visibility
stale state-dependent Plan write can silently overwrite current meaning
Event replay causes external side effect
permanent multiple authoritative Domain write languages are required
permanent bidirectional dual authority is required
```

If both pass, compare total structural + migration + operational complexity rather than LOC alone.

Evidence must record at least:

```text
authoritative write/read paths touched
compatibility exceptions
schema/mechanism count
rebuildability
scope isolation
provenance spoof resistance
stale-write behavior
replay safety
migration/rollback complexity
replication coupling
operator burden
blast radius
```

The implementation author cannot be the sole evaluator.

Current GitHub work split:

```text
kawa#2              umbrella F-006 comparison gate
memory-broker#52    Candidate A: Broker extension
kawa#3              Candidate B: greenfield Kawa
kawa#4              independent comparison review
```

The independent reviewer may conclude:

```text
broker_extension
greenfield_kawa
insufficient_evidence
```

No candidate may declare itself the winner.

## 22. Architecture Review Gate (F-001)

The architecture process itself is gated.

A review Finding is not closed because its author says it is closed. Closure must be reproducible by an independent reviewer against the current authoritative tree and evidence.

Before production-oriented implementation/cutover proceeds:

```text
no unresolved CRITICAL/HIGH architecture Finding
Security contracts internally consistent
F-006 comparative prototype decision recorded
Migration/coexistence and rollback explicit
Document authority unambiguous
Independent adversarial re-verification passes
```

A self-review by the authoring reasoning path does not satisfy independence.

Prototype work used strictly to generate F-006 architecture evidence is non-production and must remain isolated from production authority.

## 23. Publication Boundary

Kawa's security must remain effective when its architecture, protocols, schemas, and security model are public.

```text
Security architecture / invariants → public
Secrets / live security state       → private
Operational weakness / exceptions   → private
```

Repository examples MUST use synthetic data only.

Do not publish real:

```text
credentials
private keys
JWTs
hostnames/private addresses
Node/Workload identifiers
hardware inventories
production topology
DB connection strings
secret/resource mappings
security logs with exploitable detail
unpatched weaknesses
operational exceptions
```

Runtime Kawa may observe operator-specific facts; source control must not become a copy of operator Situation Awareness.

## 24. Ten-Year Compatibility Rule

Replaceable mechanics include:

```text
LLM provider
Agent runtime
MCP/transport
PostgreSQL
vector/search store
JWT implementation details
UI
Git hosting provider
```

Stable meanings include:

```text
Event
Problem
Plan
Observation
Claim
Fact
Review
Finding
Approval
Work
Result
causality
provenance
scope
```

Before adding a public concept, Event, or relation, ask:

```text
Would this still describe meaningful organizational state
if the LLM, transport, database, runtime, and UI were all replaced?
```

If not, it probably belongs in replaceable mechanics rather than stable Domain semantics.

## 25. Acceptance Invariants

A conforming architecture/implementation must be able to demonstrate at least:

```text
Projection deletion + replay reconstructs equivalent current semantics.
A new LLM can orient without prior conversation history.
A caller cannot choose trusted Node/Workload/observer identity.
A copied bearer JWT alone cannot impersonate a production Workload.
A deterministic collector Observation cannot be forged by ordinary LLM input.
An LLM inference is represented as Claim, not Observation.
A Claim cannot become Fact solely by self-reported confidence.
Omitted Project never widens visibility.
Unauthorized objects do not influence ranking.
A stale Plan revision is rejected semantically without caller-managed CAS.
A basis for one Work/Plan cannot authorize another.
High-risk Approval becomes stale when any mandatory bound semantic value changes.
Event replay never repeats external side effects.
Git merge/PR approval does not silently become Kawa Plan completion/Approval.
Migration never creates permanent dual authority.
An independent reviewer can reproduce architecture-gate closure.
```

## 26. Current Normative Documents

This specification is the consolidated architecture authority. Detailed contracts remain normative where they refine these rules without contradicting them.

Primary documents:

```text
core-logical-schema-v0.3.md
event-taxonomy-v0.2.md
reducer-projection-contract-v0.2.md
postgresql-physical-schema-v0.3.md
consistency-and-authority-v0.1.md
operation-effect-identity-v0.8.md
subject-identity-and-lineage-v0.1.md
event-log-and-replication-v0.1.md
emit-enforcement-contract-v0.1.md
mcp-contract-v0.2.md
wizard-error-guidance-v0.2.md
security-model-v0.1.md
identity-credential-lifecycle-v0.1.md
scope-resolution-v0.1.md
approval-binding-v0.1.md
stale-write-guard-v0.1.md
epistemic-claim-model-v0.1.md
deterministic-observation-ingestion-v0.1.md
llm-write-input-minimization-v0.1.md
git-plan-workspace-v0.1.md
github-workflow-integration-v0.1.md
memory-broker-extend-vs-kawa-v0.1.md
memory-broker-migration-coexistence-v0.1.md
prototype-vertical-slice-v0.1.md
architecture-review-findings-v0.1.md
```

Authority/version relationships are maintained in `supersession-matrix-v0.1.md`.

If this specification and a detailed current contract disagree, the repository is inconsistent and the architecture gate remains open until the contradiction is explicitly resolved.

## 27. Core Maxims

```text
The Agent supplies intent, not bookkeeping.
Kawa fills context; infrastructure attaches trust.
Identity is attached, never declared.
Provenance is established by execution, never claimed by text.
Hooks wake workers. Kawa holds the work.
Plans in Kawa. Artifacts in Git. References between them.
Replicate Events. Rebuild understanding.
Review closure is something the next reviewer can reproduce, not something the author can declare.
```

> **Kawa records what happened, then continuously reconstructs the best available understanding of what the world is now.**
