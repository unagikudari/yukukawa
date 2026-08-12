# Kawa Specification v0.5

Status: Draft, current consolidated architecture specification
Supersedes: `specification-v0.4.md`
Date: 2026-08-12

> ゆく川の流れは絶えずして、しかも元の水にあらず。

> **Change the agent. Keep the work.**

## 0. Executive Summary

Kawa is an event-sourced continuity, retrieval, coordination, and authority substrate for organizations where Humans and AI Agents act over time.

Kawa exists so work can continue when Humans, Agents, runtimes, models, Nodes, sessions, and tools are replaced, disconnected, partitioned, or lost.

Kawa does **not** own objective truth, a canonical Narrative, or organization-wide Situational Awareness. It preserves attributed records, relations, authority evidence, Plans, and coordination state so observers can construct their own Situational Awareness and continue work.

The primary outcomes are:

```text
Resilient organizational continuity
Inspectable decision lineage
Replaceable Humans / Agents / runtimes
Accountable execution
Fast, bounded context retrieval
Progress without a central Agent king
```

Kawa's stable architectural rule remains:

> **Stable semantics. Replaceable mechanics. Durable Events. Rebuildable projections.**

---

## 1. Design Constitution

```text
One Domain Source of Truth: Events.
One canonical Domain write primitive: Emit.
Corrections append; semantic history is not rewritten.
Kawa records phenomena and attributed decisions; it does not declare objective reality.
Kawa attributes; it does not make unattributed assertions.
Authentic provenance does not imply accurate content.
Situational Awareness belongs to observers, not to Kawa.
Narrative is authored by Humans or Agents, not by Kawa.
Kawa preserves disagreement; it does not reconcile by invention.
Derived views organize records; they do not become reality.
Kawa may reason deterministically about its own records, protocol state, authority, and mechanics.
Plans persist; Agents do not.
Agents coordinate through shared state, not conversation.
Context is pulled, not pushed.
Guidance is just in time.
Constraints belong in the system, not in prompts.
Default to current; history is explicit.
Derived state must be disposable.
The working set may forget; accountability may require durable archival history.
Retrieval relevance is not truth.
One semantic intent should have one obvious tool.
Expose semantic choices; hide mechanical choices.
Push for liveness. Pull for correctness.
Wake the runtime, not the model context.
Participants discover Work, not participants.
No king does not mean no coordination.
```

A design that violates one of these rules requires an explicit architecture revision; it is not an implementation detail.

---

## 2. Epistemic Boundary

Kawa owns records about interaction with reality. It does not claim possession of reality itself.

Conceptually:

```text
Reality / environment
      ↓
Human / Agent / sensor / external system
      ↓
Observation / Claim / Plan Events
      ↓
Kawa records + relations + provenance
      ↓
retrieval bundle
      ↓
Human / Agent observer
      ↓
Situational Awareness / Narrative / decision
```

Kawa is an epistemic witness, not an epistemic judge.

### 2.1 Core epistemic concepts

The preferred minimal epistemic nucleus is:

```text
Event
Observation
Claim
Plan
```

Other organizational/security concepts may exist where they have independent operational semantics, but they MUST NOT duplicate Observation, Claim, or Plan merely for naming convenience.

### 2.2 Observation

An Observation records what an attributed observer, collector, adapter, process, or external system measured, received, perceived, or reported.

Observation is not Truth.

```text
Observation != objective reality
Authenticated observer != accurate observer
Authentic provenance != correct content
```

A deterministic collector may provide stronger provenance than self-reported text, but Kawa does not convert provenance strength into epistemic Truth.

Typical fields include:

```text
subject_ref
observer_ref
observation_method
predicate / content
value?
occurred_at
source_ref?
```

### 2.3 Claim

A Claim is an attributed assertion, interpretation, inference, assessment, recommendation, rationale, or proposition.

Examples:

```text
"The database is saturated."
"The certificate rotation is the likely cause."
"Given the current evidence and rollback cost, failover is preferable."
```

A Claim need not be true.

Self-reported confidence is assertion content, not trusted authority.

### 2.4 Reason is a Claim in relation to a Plan

Kawa does not require an independent `Reason` Domain noun.

A rationale is represented as a Claim linked to a Plan:

```text
Claim --reason_for--> Plan
```

This avoids a special-case semantic class while preserving decision lineage.

Claim answers:

```text
What does this observer assert?
```

`reason_for` answers:

```text
Which Claim was recorded as rationale for this Plan?
```

The same Claim MAY be reason for multiple Plans.

### 2.5 Outcome collapses into Observation

Kawa does not require an independent epistemic `Outcome` noun merely to record what was seen after action.

After execution, Kawa records Observations such as:

```text
command exit code = 0
health endpoint returned 200
error rate = 0.006
PR state = merged
```

An assertion such as "service recovered" is a Claim supported by relevant Observations.

Therefore:

> **Outcome is an Observation seen after action. Reason is a Claim used to choose a Plan.**

Execution receipts may still exist as security/coordination mechanics where they carry independent proof semantics.

### 2.6 No Fact projection

Kawa does not define a privileged `Fact` projection meaning "what Kawa currently accepts as true".

There is no ordinary `Fact` Domain object and no Kawa-owned `Current Understanding` that claims epistemic primacy.

Kawa MAY deterministically report facts about its own internal records, for example:

```text
Claim C8 exists.
C8 is linked reason_for P17.
No applicable Kawa Approval exists at frontier F.
Work W4 is not eligible under the current capability projection.
```

These statements concern Kawa's records/protocol state, not objective reality.

### 2.7 Narrative

Narrative is authored by a Human or Agent, not by Kawa.

Kawa MAY preserve an authored Narrative, brief, report, summary, or other artifact as attributed content when accountability requires it.

An authored Narrative is not promoted into Truth merely because it is stored by Kawa.

Kawa preserves the recorded basis of an observer's decision; it does not claim to reconstruct the observer's complete mental state.

---

## 3. Public Semantic Model

The minimal preferred public concepts are:

```text
Project
Plan
Observation
Claim
Work
Approval
Review / Finding where independent governance semantics are required
```

`Problem` MAY remain as an organizational interpretation with an independently evolving lifecycle where it provides concrete user value; it MUST NOT be treated as objective reality.

Mechanics remain internal unless an operator/debug interface explicitly needs them:

```text
lease
fencing token
CAS / expected revision
projection cursor
idempotency key
semantic fingerprint
replication cursor
index frontier
attention aggregation internals
session routing internals
credential internals
```

A concept receives enduring identity only when it has an independently evolving lifecycle across multiple Events.

---

## 4. Event Source of Truth and Projections

Events are the only durable Domain Source of Truth.

Derived views include:

```text
current Plan lifecycle
Work eligibility
Plan progress
search indexes
relation neighborhoods
attention projections
runtime presence
availability manifests
Console views
```

They may be implemented as SQL VIEWs, materialized views, reducer-maintained tables, caches, indexes, or external replaceable projections, but MUST remain disposable and reconstructable from durable inputs plus explicitly declared non-Domain trust/policy inputs where applicable.

Corrections create later Events with typed semantic links such as:

```text
corrects
supersedes
contradicts
```

No semantic Last Write Wins is permitted for unresolved disagreement.

---

## 5. OODA and Cognitive Lineage

Kawa supports OODA without itself becoming the observer or decision-maker.

```text
Observe
  Observation

Orient
  Human/Agent evaluates Observations and Claims
  → new Claim(s)

Decide
  Plan
  ← reason_for Claim(s)

Act
  Work → trusted/authorized execution

Observe again
  new Observation(s)
```

The durable lineage is a typed graph, not necessarily a tree.

Typical relations include:

```text
supports
contradicts
based_on
reason_for
addresses
reviews
corrects
resolves
supersedes
result_of / caused_by where mechanically meaningful
```

Evidence relation and decision relation MUST remain distinct:

```text
Observation --supports--> Claim
Claim --reason_for--> Plan
```

Semantic similarity alone MUST NEVER create one of these Domain relations implicitly.

---

## 6. Plan, Work DAG, and Progress

### 6.1 Plan

A Plan is structured organizational intent.

A Plan should express at least, where applicable:

```text
objective
scope
constraints
success criteria / expected observations
reason_for Claim references
required capabilities at the Plan or derived Work level
lifecycle
```

Prefer `expected_observations` / typed `success_criteria` over an authoritative-looking `expected_result` assertion.

Plan semantics are persisted; executor-specific prompts are not the Plan.

> **Persist the meaning; render the prompt.**

### 6.2 Work

Work is a derived executable opportunity.

```text
Plan
  ↓ derive
Work DAG
  ↓ eligibility
capable / authorized participant
  ↓ execution
new Observation / Claim / coordination Event
  ↓
recompute eligibility
```

Work is normally addressed by capability, authority, scope, and dependency state rather than by mortal Agent identity.

> **Participants discover Work, not participants.**

A particular principal MAY be required when the semantics truly require that authority, such as a specific Human approver.

### 6.3 Plan progress is derived

Plan progress is a projection over the Work DAG and recorded Events.

Kawa MUST NOT require an independently authoritative `65% complete` Domain state.

UI progress percentages or summaries are disposable projections.

The Work structure is a DAG even when the Console renders it as a tree.

### 6.4 Requests, acceptance, rejection, blocking

Request/accept/reject/block/retry are coordination occurrences, not necessarily new enduring Domain nouns.

They MAY be represented by typed coordination Events where operational semantics require them.

The rationale for rejection or blocking is represented as one or more Claims linked to the relevant coordination occurrence or Plan/Work.

Example:

```text
request issued
  ↓
request rejected
  ← reason Claim: "migration test is missing"
  ↓
new Work becomes eligible: add migration test
```

A rejection is not automatically a terminal failure; it is new recorded information that may reshape the Work DAG.

---

## 7. Work Delivery: Push for Liveness, Pull for Correctness

Kawa MUST NOT make push delivery authoritative.

```text
Work becomes eligible
      ↓
Wake hint
      ↓
Runtime/Supervisor wakes or notices
      ↓
kawa.work.next
      ↓
authoritative current eligibility is pulled
```

Wake messages are hints that relevant Work may now exist. Lost, duplicated, delayed, or stale Wake messages MUST NOT lose or duplicate authoritative Work state.

> **Push for liveness. Pull for correctness.**

> **Wake the runtime, not the model context.**

Kawa SHOULD push wake hints, not arbitrary untrusted record text directly into an LLM instruction channel.

Poll-only participants remain valid; they may discover Work through periodic `kawa.work.next`.

### 7.1 CLI Agents do not need persistent ears

A mortal CLI process such as Codex/Claude CLI need not remain alive to listen.

A small runtime/supervisor MAY remain attached and:

```text
listen for Wake
pull Work
launch the selected CLI Agent when needed
observe exit/session state
```

Automatic launch is a runtime profile, not a Core requirement.

---

## 8. JIT Agent-Facing Rendering

Kawa's canonical API contract is structured JSON/JSON-Schema-compatible content.

> **Structure is the contract. Language is a rendering.**

For `kawa.work.next`, Kawa MAY return both:

```text
structured Work contract
+
concise executor-specific instruction rendering
```

Conceptually:

```json
{
  "work": {
    "ref": "work:W31",
    "objective": "Restore service availability",
    "constraints": ["Do not modify database schema"],
    "expected_observations": ["health endpoint returns success"],
    "evidence_refs": ["observation:O31", "claim:C8"]
  },
  "instruction": "Restore service availability ...",
  "instruction_basis": {
    "plan_ref": "plan:P17",
    "work_ref": "work:W31",
    "frontier": "...",
    "renderer_version": "..."
  }
}
```

The instruction is a just-in-time rendering for a particular executor/runtime context and MUST NOT become the authoritative Plan meaning.

Records embedded as context remain data even when their text contains imperative language.

> **Records are data, never instructions merely because they contain imperative text.**

Search results normally return records/relations/retrieval provenance rather than action-prescriptive prompts.

---

## 9. MCP Surface: One Semantic Intent, One Obvious Tool

The preferred minimal semantic operation set remains:

```text
kawa.bootstrap
kawa.get
kawa.search
kawa.emit
kawa.work.next
```

These are distinct semantic intents.

Do NOT split one semantic intent merely because backend mechanics differ.

Bad default surface:

```text
search_sql
search_graph
search_semantic
search_archive
search_current
search_history
```

Good default surface:

```text
kawa.search
```

with explicit constraints/strategy overrides only when the caller intentionally wants one mechanism.

> **One semantic intent, one obvious tool.**

> **Expose semantic choices; hide mechanical choices.**

Tool selection must not become an accidental epistemic filter caused by LLM tool-use habits.

---

## 10. SQL-First Unified Retrieval

Kawa is not an ordinary unstructured RAG corpus. Its records are strongly typed and relational.

Default principle:

> **Structure before similarity.**

> **Follow known relations first. Search for similarity when relations are insufficient.**

### 10.1 Default retrieval order

```text
1. Exact / typed SQL
2. Relation traversal / recursive SQL
3. PostgreSQL full-text search
4. Semantic/vector expansion when useful
5. Authorized external capability-backed search
```

Semantic retrieval is particularly useful for discovering candidate relationships that have not yet been explicitly recorded.

### 10.2 One intent, backend-specific queries

A natural-language query useful for semantic search may be poor input for SQL or FTS.

Therefore:

```text
search request
    ↓
context extraction
    ↓
retrieval planner
    ├─ typed SQL
    ├─ graph traversal seed / relation constraints
    ├─ FTS query
    ├─ semantic query rewrite
    ├─ archive lookup
    └─ external capability query
```

> **One search intent may produce many mechanically appropriate backend queries.**

LLMs specify intent and constraints, not retrieval mechanics.

Backend rewrites/planner decisions are retrieval mechanics, not Domain Claims.

Where useful, retain traceability:

```text
original request
resolved intent
focus/context
backend query/rewrite
planner version
retrieval mechanism
frontier/index freshness
```

### 10.3 Preserve retrieval provenance

Do not flatten all candidates into epistemically anonymous hits.

Results should preserve how they were found:

```text
structural path
lexical match
semantic similarity
historical branch
external source
attention/reuse signal
freshness/frontier
visibility basis
```

A semantic candidate with no known structural relation is not equivalent to a record reached by explicit `supports` / `reason_for` lineage.

### 10.4 Ranking is non-epistemic

Ranking MAY use:

```text
relation distance/type
structural centrality
recency
independent attention/reuse
lexical relevance
semantic relevance
query context
```

Rules:

```text
retrieval relevance != truth
semantic similarity != relation
popularity != correctness
selection != acceptance
```

A fused score is disposable retrieval mechanics, not a durable Domain Truth score.

### 10.5 Attention graph is separate from Domain graph

Retrieval/use activity MAY be recorded in an attention/retrieval projection or log for ranking and storage placement.

Do not manufacture Domain evidence relations merely because a record was frequently retrieved.

Independent reinforcement SHOULD resist self-amplification; raw hit counts and raw distinct Node counts are insufficient due to feedback-loop and Sybil risks.

TrustRank/EigenTrust/PageRank-like mechanics may inform replaceable retrieval algorithms, but Kawa MUST NOT reinterpret their scores as Truth or correctness.

### 10.6 Vector indexing is optional and asynchronous

Semantic/vector indexing MUST NOT block Event recording.

The initial implementation SHOULD be able to operate PostgreSQL-only:

```text
PostgreSQL
  + typed schema
  + indexed relations
  + recursive SQL
  + FTS
  + attention/retrieval projections
```

Vector indexing may be added when measured query classes demonstrate insufficient recall.

For immutable semantic content, embedding SHOULD generally be computed once per content/model identity and reused where safe; identical embedding content MUST NOT collapse distinct attributed Domain records.

Index freshness MAY lag the Event log and SHOULD be inspectable where relevant.

---

## 11. Retrieval Lifecycle vs Accountability Retention

Retrieval accessibility and retention obligation are separate axes.

```text
retrieval lifecycle:
  hot → warm/latent → archive

retention obligation:
  policy/accountability driven
```

> **Decay attention. Preserve accountability.**

> **Retrieval lifecycle != retention lifecycle.**

Low attention MAY move records out of the active working set without permitting destruction.

A record used in material decision lineage, approval, security evidence, or regulated accountability may require long retention even if it is rarely retrieved.

Physical erasure MAY still be required by privacy, legal, contractual, secret-destruction, or retention policy. Semantic corrections append; physical retention policy is a separate concern.

---

## 12. Basin, Replication, Selective Materialization, and Archive

A Basin is a set of Kawa Nodes participating in a shared continuity/discovery domain without requiring identical state, identical authority, or global consensus.

```text
Basin membership != replication equality
Basin membership != authority membership
Basin membership != data access
Reachability != Basin membership
```

Events belong to origin lineages. Basins grant participation and visibility over authorized parts of those lineages.

### 12.1 Merge and split

Basin split/merge MUST NOT rewrite Event origin histories.

> **Basin merge != Event-history merge.**

A merge makes authorized histories discoverable/shareable; it does not require every Node to materialize every Event.

### 12.2 Selective materialization

Universal full-history replication is not a Kawa requirement.

Preferred direction:

> **Preserve records. Replicate authorized commitments broadly within their disclosure domain. Materialize selectively.**

Nodes may hold different working sets according to capability, scope, storage capacity, and policy.

Conceptually:

```text
active/local records
known remote records
archived records
unknown/not-disclosed records
```

A Node may know that an authenticated segment exists without locally storing the full segment, subject to authorization and metadata-disclosure policy.

### 12.3 Segment commitments and archive

Long origin histories SHOULD support authenticated segment commitments so a lightweight Node can retain verifiable lineage metadata while accountable history is stored on archive-capable Nodes/object storage.

The exact authenticated data structure (Merkle tree, Merkle mountain range, chained segment root, equivalent) is replaceable mechanics.

Commitment existence does not prove durability. Archive availability SHOULD be checked through restore/read/proof observations according to policy.

### 12.4 Metadata obeys disclosure boundaries

Frontiers, segment existence, holder manifests, Project existence, and volume metadata can leak sensitive information.

Metadata MUST obey disclosure constraints compatible with the underlying records.

---

## 13. Node Identity and Incarnation

Node logical identity and a concrete continuity lineage are distinct.

A VM snapshot or disk clone can otherwise produce two successors with the same Node key/origin sequence.

Preferred conceptual identity:

```text
Node Identity
Node Incarnation
origin sequence within incarnation
previous commitment/hash
Event signature/authenticity proof
```

A same-incarnation fork such as:

```text
same node
same incarnation
same origin_seq
but different authenticated successors
```

is evidence of equivocation/fork and MUST NOT be silently resolved by Last Write Wins.

A restore, migration, or intentional fork SHOULD start a new incarnation linked by explicit succession/parentage evidence where possible.

---

## 14. Identity, Runtime Presence, and Process Incarnation

Identity layers are distinct:

```text
Human Identity
Node Identity
Node Incarnation
Workload Identity
Runtime Identity
Process Incarnation
Logical Agent Identity
MCP Session
```

Not every layer is a durable Domain entity; this is a trust/coordination model.

Logical Agent identity is descriptive/accountability continuity and is not the primary authorization root.

Workload Identity remains the primary NHI authorization boundary.

### 14.1 Do not authenticate PID

PID is local observational metadata only.

PIDs may be reused and may differ across namespaces/containers.

> **Do not authenticate a PID. Authenticate a process incarnation, and merely record its PID.**

A Process Incarnation should have an unambiguous lifetime identity, normally including a fresh random identifier and/or ephemeral proof key.

A restarted process is a new incarnation even if executable, Node, Runtime, Logical Agent, or PID are the same.

### 14.2 Process-bound proof

A process/runtime MAY generate or receive an ephemeral key pair for proof-of-possession.

The process may possess the ephemeral private proof key. It MUST NOT possess issuer signing authority.

> **Agents may possess ephemeral proof keys. They must not possess issuer authority.**

---

## 15. Credential Issuance and Secret Mediation

### 15.1 Identity/Credential Broker

Short-lived Workload/Process credentials MUST be issued by a trusted Identity/Credential Broker or equivalent trusted issuer, not self-issued by the Agent.

Conceptual flow:

```text
process starts
  ↓
trusted Runtime/Supervisor observes/binds Process Incarnation
  ↓
Identity/Credential Broker
  ├─ verifies Node / Runtime / Workload context
  ├─ binds Process Incarnation
  ├─ binds ephemeral public proof key
  ├─ resolves authorized capability context
  └─ signs short-lived credential
  ↓
Agent/process receives bounded credential
```

A JWT profile may contain claims conceptually equivalent to:

```text
subject = Process/Workload Incarnation
node
runtime
workload
pid as non-authoritative metadata
cnf.jkt or equivalent PoP binding
scope/capability claims
short expiry
```

### 15.2 Secret Broker

Secret custody and identity credential issuance are distinct responsibilities even if one implementation provides both.

```text
Identity/Credential Broker
  → identity / workload credential issuance

Secret Broker / trusted Adapter
  → custody and mediated use of external secrets
```

Raw long-lived GitHub tokens, cloud secrets, private issuer keys, and similar credentials SHOULD NOT be disclosed to Agents.

Trusted Adapters/Secret Brokers perform authorized operations using protected credentials internally where feasible.

### 15.3 Production credential profile

The production direction remains:

```text
Human-authorized enrollment
TPM-backed Node key where supported
Ed25519 / EdDSA signing profile
JWKS/public-key distribution as applicable
short-lived Workload credential
cnf.jkt / equivalent key binding
DPoP-style proof-of-possession or equivalent runtime-bound proof
mandatory revocation
```

A copied JWT string alone is insufficient production Workload authentication.

---

## 16. MCP Initialization as Participant Introduction

MCP initialization/connection establishment SHOULD exchange enough information to construct trusted participant context without requiring the LLM to repeat it in prompts.

Conceptually exchange four distinct categories:

```text
Identity
Capability
Reachability
Introduction
```

### 16.1 Identity

Identity is authenticated/attested context, not merely Agent-provided text.

Examples:

```text
workload_ref
runtime_ref
process_incarnation_ref
logical_agent_ref as descriptive metadata
node context
```

### 16.2 Capability

The participant MAY advertise what it knows how to do, but advertised capability is not authority.

Kawa reconciles:

```text
advertised ability
verified/mediated capability
current authorization
scope
```

Only the resulting authorized capability context contributes to Work eligibility.

### 16.3 Reachability

An active MCP session MAY serve as ephemeral reachability.

> **The session can be the address.**

Persistent Wake reachability is optional and may be advertised by a Runtime/Supervisor when supported.

Network endpoint strings are replaceable reachability mechanics and MUST NOT become semantic identity.

### 16.4 Introduction

A Human/Agent/Runtime MAY provide attributed self-description such as:

```text
kind
skills/preferences
specialization
operating constraints
```

Self-description MAY inform routing heuristics but MUST NOT grant authority.

> **Introduction informs routing. Identity and capability determine authority.**

### 16.5 Capability reconciliation example

Conceptually:

```text
Agent: "I know how to use Git."

Kawa trusted context:
  git.read = allowed
  git.commit = allowed
  git.push = denied
```

The LLM need not repeatedly assert its Node, PID, Workload, or capability context in every tool call when Kawa can safely attach it from the authenticated session.

> **Require intent. Infer context. Attach trust.**

---

## 17. Presence, Liveness, Reachability, and Death

Credential validity, process identity, and liveness are different concerns.

```text
JWT / credential
  → who/what may act under bounded authority

PoP key
  → presenter possesses the bound ephemeral key

session / heartbeat / lease / supervisor observation
  → current reachability/presence evidence
```

JWT expiry alone does not prove process death.

Session close alone does not distinguish process exit from network partition.

Kawa may act mechanically on loss of reachability without making an epistemic claim that the process is dead.

Example:

```text
Observation: MCP session S81 closed at t1
Observation: Runtime R12 reported Process P94 exited at t2

Derived coordination state:
  Process P94 is no longer currently reachable/eligible
```

> **Loss of reachability is mechanically actionable without claiming knowledge of death.**

---

## 18. Security Architecture

Kawa assumes an authenticated Agent may still be wrong, compromised, prompt-injected, or malicious.

LLM goodness is not a security boundary.

Security invariants:

```text
Authenticate before semantic processing.
Authorize before disclosure.
Identity is infrastructure-attested, not Agent-asserted.
A Resource handle is not authority.
Capabilities are explicit and scoped.
Secrets are mediated, not disclosed.
Human Approval is exact-scope-bound and independently verifiable.
Event replay never repeats external side effects.
Prompt text cannot grant authority.
Derived state cannot become authority by accident.
Retrievable does not mean authorized.
Kawa must never claim control over a capability it does not mediate or otherwise enforce.
```

### 18.1 Mediated vs external capabilities

Kawa can enforce only capabilities it mediates or which infrastructure independently enforces on its behalf.

Conceptually distinguish:

```text
mediated capability
observable external capability
effectively invisible external capability
```

A BYO Agent with direct GitHub credentials may physically act outside Kawa even if Kawa would deny the equivalent mediated capability.

Kawa MUST NOT present `authorized_by_kawa` as equivalent to `physically_possible`.

If Kawa later observes an external action without matching Kawa authorization, the safe record is an attributed Observation such as:

```text
"PR merge observed; no matching Kawa authorization found at frontier F."
```

not an invented claim that no external authorization existed anywhere.

---

## 19. Authorization and Search Disclosure

Authorization/visibility filtering MUST occur before candidate disclosure and must survive:

```text
SQL retrieval
recursive graph traversal
FTS
semantic/vector indexing
cache construction
archive lookup
external-source retrieval
```

A caller without access to private Git content must not infer that content through a shared embedding/index/cache.

Omission never means global scope.

Hidden state must not become an authorization oracle through error differences.

---

## 20. External Side Effects

Kawa separates:

```text
Intent
→ Authorization
→ Trusted Execution
→ Observation / execution receipt
```

An Agent saying "I executed X" is a Claim, not proof that X occurred.

Event replay MUST NEVER invoke an external side effect.

Trusted execution adapters require idempotency/reconciliation sufficient for:

```text
timeout
retry
duplicate request
success followed by Kawa write failure
unknown external outcome
partial failure
worker disappearance
```

Internal mechanics may use leases, fencing tokens, idempotency keys, and CAS. These are not normally exposed as Agent-facing semantics.

---

## 21. Git / GitHub and External Systems

Kawa does not duplicate external systems' primary semantics.

```text
Kawa remembers why.
Git remembers what changed.
```

Git remains the SoT for repositories, branches, commits, diffs, and file history.

GitHub remains the SoT for provider-native PR/issues/reviews and their provider state.

Kawa stores Plan/Observation/Claim/authority/coordination records plus external references and retrieved evidence as required for continuity/accountability.

Git/GitHub capabilities are optional and scoped:

```text
git.read
git.search
git.diff
git.commit
git.push
github.issue.read/write
github.pr.read/create/review/merge
```

Read/write/merge authority MUST remain distinct.

---

## 22. Console and Human Presentation

The Console is a projection over Kawa records and runtime/security state.

UI wording MUST avoid implying that Kawa owns Truth or Situational Awareness.

Prefer surfaces such as:

```text
Available Evidence
Current Claims
Active Plans
Work / Progress
Cognitive Lineage
Authority
Runtime / Presence
Retrieval
Fleet / Basin
Archive / Accountability
```

Human-facing rendering MAY use Markdown/YAML-like presentation, but canonical API semantics remain structured.

---

## 23. Implementation Sequence

The current design revision changes semantics more than storage mechanics. Do not restart the repository from scratch.

> **Rewrite the epistemology, not the repository.**

Recommended dependency order:

```text
0. reconcile current physical schema with event/replication contracts
1. replace Fact / Kawa-owned Current Understanding semantics
2. converge Event / Observation / Claim / Plan + typed relation model
3. SQL-first unified retrieval and retrieval provenance
4. Plan/Work DAG + coordination Events + JIT Work rendering
5. Workload/Runtime/Process Incarnation identity and trusted credential issuance
6. MCP participant initialization / capability reconciliation / presence
7. Wake push + authoritative pull runtime loop
8. real Node identity + Node Incarnation + 2-node replication
9. Basin admission / selective materialization / archive commitments
10. distributed Authority Cells where needed
11. semantic/vector retrieval only where measured recall requires it
12. stronger durability/scale/assurance and real dogfood
```

---

## 24. Candidate Maxims

> **Kawa must not lie.**

> **Kawa records. Kawa does not conclude objective reality.**

> **Kawa attributes. Kawa does not assert without attribution.**

> **Authentic provenance does not imply accurate content.**

> **Situational Awareness belongs to observers.**

> **Derived views organize records; they do not become reality.**

> **Outcome is an Observation seen after action. Reason is a Claim used to choose a Plan.**

> **Structure before similarity.**

> **Follow known relations first. Search for similarity when relations are insufficient.**

> **One semantic intent, one obvious tool.**

> **One search intent may produce many mechanically appropriate backend queries.**

> **LLMs specify intent and constraints, not retrieval mechanics.**

> **Do not let tool selection become an accidental epistemic filter.**

> **Retrieval relevance is not truth.**

> **Persist the meaning; render the prompt.**

> **Push for liveness. Pull for correctness.**

> **Wake the runtime, not the model context.**

> **Participants discover Work, not participants.**

> **Agents may possess ephemeral proof keys. They must not possess issuer authority.**

> **Do not authenticate a PID. Authenticate a process incarnation, and merely record its PID.**

> **Introduction informs routing. Identity and capability determine authority.**

> **The session can be the address.**

> **Loss of reachability is mechanically actionable without claiming knowledge of death.**

> **Basin merge is not Event-history merge.**

> **Decay attention. Preserve accountability.**

---

## 25. Status Discipline

This document is a design specification. A section being present here means **DESIGNED**, not necessarily IMPLEMENTED, INTEGRATED, DEPLOYABLE, DOGFOODED, or PUBLIC.

Current implementation claims MUST be verified separately against repository code, migrations, tests, live multi-node runs, and operator-visible behavior.

---

## 26. Current Document Set

The detailed contracts currently in force alongside this specification (the supersession
matrix is the authority map; this list exists so spec, matrix, and README cannot drift
apart silently — the C4 document-set lint compares all three):

```text
approval-binding-v0.1.md
architecture-adversarial-review-v0.2.md
architecture-review-findings-v0.1.md
consistency-and-authority-v0.1.md
console-read-model-v0.1.md
core-logical-schema-v0.3.md
deterministic-observation-ingestion-v0.1.md
document-versioning-and-reading-path-v0.1.md
emit-enforcement-contract-v0.1.md
epistemic-claim-model-v0.2.md
event-log-and-replication-v0.1.md
event-taxonomy-v0.2.md
git-plan-workspace-v0.1.md
github-workflow-integration-v0.1.md
identity-credential-lifecycle-v0.1.md
llm-write-input-minimization-v0.1.md
mcp-contract-v0.2.md
memory-broker-architecture-evidence-v0.1.md
memory-broker-extend-vs-kawa-v0.1.md
memory-broker-migration-coexistence-v0.1.md
operation-effect-identity-v0.8.md
postgresql-physical-schema-v0.3.md
prototype-vertical-slice-v0.1.md
reducer-projection-contract-v0.2.md
scope-resolution-v0.1.md
security-model-v0.1.md
stale-write-guard-v0.1.md
subject-identity-and-lineage-v0.1.md
user-value-and-onboarding-v0.1.md
wizard-error-guidance-v0.2.md
```
