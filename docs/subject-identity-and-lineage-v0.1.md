# Kawa Subject Identity and Lineage v0.1

Status: Draft, normative candidate — the root data structure the write path, consistency model, and authority scoping are all defined over
Scope: What a `subject_ref` is, how it is minted, how subject meaning and lineage evolve without ever mutating identity, and how the authority/serialization domain is derived from a subject rather than equated with it.
Informed by: research note #27 (W3C PROV, RFC 9562 UUIDv7, IPFS content-addressing, DDD aggregates — inputs, not authorities).
Companions: `event-log-and-replication-v0.1.md` (the envelope carrying `subject_ref`), `emit-enforcement-contract-v0.1.md` (the write path this refines), `epistemic-claim-model-v0.1.md` (equivalence as Fact).

> Identity is immutable. Meaning evolves through Events.

## 0. Why this is the root slab

Everything above it is defined *over* a subject: the emit path serializes per subject, the consistency model scopes authority per subject, the entity model (Project, Problem, Plan, Review, Finding) *is* a set of subjects. If "what a subject is" is soft, all of them are built on sand — the per-subject lock has an undefined domain, and "concurrent writes to the same subject" has no meaning if a subject can silently split or merge. So this is poured before the consistency/authority slab and before the emit keystone is finalized. Dependency order:

```text
Event atom → Subject Reference → Subject Lineage → Operation → Invariant → Authority key → Emit
```

The one structural commitment that makes the rest tractable:

> **Separate the immutable Subject Reference from the evolving Subject Meaning/Lineage.** Identity is a permanent opaque handle; meaning, equivalence, and lineage are reconstructed from Events, never baked into the handle.

## 1. Layer 1 — Subject Reference (immutable)

```text
subject_ref
  = an immutable, opaque, globally-unique Kawa identifier (UUIDv7)
  = mintable offline, with no global coordination
  = identifies one Kawa-recognized characterization, for the life of the log
```

A `subject_ref` is **minted by Emit** when a subject-creating event first occurs (`project.created`, `problem.raised`, `plan.proposed`, `review.started`, `finding.raised`). The creating event's `subject_ref` *is* the new identity. It is UUIDv7 for the same reasons as `event_id` (`event-log-and-replication §4.3`): offline-mintable, time-sortable, practically collision-free, node-independent.

A `subject_ref` grants **nothing**. Holding or knowing one does not imply:

```text
visibility        scope             authority
truth             current revision  real-world uniqueness or equivalence
```

Two disconnected nodes may each mint a `subject_ref` for what is later understood to be the same real-world thing. **That is not an id collision** — it is an entity-resolution question, answered later by evidence and explicit relations (§3), never by a coordinated allocator. This is what makes subject minting fully offline-safe and keeps it on the AP evidence plane: no node ever waits to name a subject.

> **Identity identifies. Policy authorizes. Evidence resolves equivalence.**

## 2. Layer 2 — Subject Meaning and Lineage (event-derived)

The meaning of a subject — its current characterization, its history, its relationships to other subjects — is **reconstructed from Events and explicit typed relations**, exactly as Fact is (`epistemic-claim-model`). Identity stays fixed; meaning only ever *acquires* Events and relations. It is never edited in place.

### 2.1 Lineage relations (Core v0.1, minimal)

Carried as typed semantic links (`event_links`) on lineage Events; never rewriting the `subject_ref` of any prior Event:

```text
split_from     B, C were carved out of A            (A --split_from<-- B, C)
merged_from    F consolidates D, E                   (F --merged_from--> D, E)
supersedes     B is the successor characterization of A
derived_from   B was produced from A
alternate_of   A and B are two views of one thing (equivalence-lineage, §3)
```

Split, merge, reclassification, and replacement therefore **create new identities plus lineage relations** — they do not mutate old ones. A Problem reframed into two Problems mints B and C with `split_from A`; every Event ever recorded about A still reads `subject_ref = A`, forever. History is intact by construction, not by discipline.

### 2.2 Why not mutate the canonical subject
Making `subject_ref` an eternal, mutable, canonical description of a real-world thing (the rejected Alternative in #27) forces identity to change when understanding changes — which rewrites the meaning of every past Event that referenced it, breaking immutability and audit. Immutable identity + event-derived lineage gives the same expressive power (you can always follow lineage to the current characterization) without ever falsifying the past.

## 3. Equivalence is epistemic, never a silent merge

If two nodes independently mint `subject_A` and `subject_B` for the same apparent thing, Kawa MUST NOT silently collapse them.

```text
same_as_candidate   a CLAIM (or deterministic Observation) that A and B denote one thing
equivalence Fact     a PROJECTION over such claims + evidence + policy (clear | conflicted | unknown)
canonicalizes_to     a GOVERNED, authoritative decision to treat B as canonical for A
```

Two distinct levels, deliberately separated:

- **Read-equivalence** — orientation and Situation Awareness may present A and B as one composed view when the equivalence Fact is `clear`. This is a projection; it changes no identity and no authority.
- **Authority-merge** — `canonicalizes_to` is a CP-plane, governed event (approval-bound, `approval-binding`). Only it makes B the authority target for A's future authoritative operations.

So two subjects can be **equivalent for reading while remaining distinct authority targets** until an accountable canonicalization. Equivalence is asserted and proven; it is never guessed. (This is the same projection pattern as trust (#21), evidence-grade (#22), and Fact itself — the log carries identity and assertions; meaning is a current projection resolved by policy.)

## 4. The authority key is derived from the subject, not equal to it

The deepest concurrency question is not "same subject?" but **"same authority / invariant domain?"** — because the same subject carries operations with different consistency needs:

```text
observation.recorded(P)   concurrent / eventual   (AP)
claim.recorded(P)         concurrent / eventual   (AP)
plan.revised(P)           serialized within its authority domain   (CP)
approval.granted(P)       serialized, bound to exact Plan semantics (CP)
```

Therefore the serialization/consistency domain is a derived key:

```text
authority_key = f(subject_ref, semantic_operation, policy, relevant_lineage)
```

- `subject_ref` names *what*; `semantic_operation` + `policy` name *which invariant applies*; `lineage` enters when a split/merge changes what "the subject" is for that invariant.
- The consistency class attached to an `authority_key` (AP vs CP, and the quorum for CP) is defined by the consistency/three-plane slab (③④), which builds directly on this key.

### 4.1 Correction to the emit keystone (#18)
`emit-enforcement-contract §2.3/§4.1` serializes per `subject_ref`. That is refined here: **Emit SERIALIZE locks on the `authority_key`, not on `subject_ref`.** For AP-family operations the key permits concurrency (node-local append, cross-node writes reconcile as an explicit causal fork, no last-write-wins); for CP-family operations the key serializes within its authority domain (quorum, per ③④). `subject_seq`'s node-local gap-freeness (`event-log-and-replication §2.2`) is unchanged; global per-subject density was never promised. This is the concrete revision the emit doc anticipated when it noted it rests on ① and ③④.

## 5. Subject identity vs Project scope

```text
subject_ref   identity — what Events are about (global, opaque)
project_ref   scope    — authorization and visibility context
```

They are orthogonal. `subject_ref` is not Project-qualified; no invariant requires it (a subject may be referenced across scopes it is authorized into). The security invariant:

> **Knowledge of a `subject_ref` is not authorization to see or act on the subject.** Authorization is resolved from `project_ref` + capability (`scope-resolution`, `security-model`), before retrieval — never inferred from possession of an id.

`(issuer, local_id)` namespace-local identity (#27 Alternative 2) is retained only as a federation fallback; the default is one opaque UUIDv7, because it satisfies offline minting with no namespace mechanics leaking into every query and reducer (Zen: one obvious id).

## 6. What this closes and opens

Closes / establishes: the domain over which emit's SERIALIZE, the consistency model, and authority scoping are defined; offline-safe subject minting (AP-plane, no coordination); split/merge/replace without rewriting history; entity resolution as an epistemic projection, not an identity mutation.

Refines: `emit-enforcement §2.3/§4.1` — SERIALIZE locks on `authority_key`, not `subject_ref` (§4.1). To be folded into #18.

Opens (the next slab, building on this): the consistency/three-plane model (③④) — it consumes `authority_key`, assigns each key a consistency class (AP evidence / CP authority), and defines the Authority Cell quorum for CP keys. Subject lineage that changes an invariant domain feeds that key.

## 7. Acceptance tests

```text
offline-mint       Two partitioned nodes each mint a subject_ref for the same real-world
                   thing; both are valid, distinct, non-colliding — no coordination occurred.
identity-immutable A subject's meaning changes across many Events; its subject_ref never
                   changes, and every historical Event still reads its original subject_ref.
split-no-rewrite   Reframe A into B and C (split_from A); assert no prior Event's subject_ref
                   was altered and A's history is fully intact.
equivalence-no-merge  Assert same_as_candidate(A,B); read/orientation may compose them, but
                   A and B remain distinct authority targets and no Event is rewritten until a
                   governed canonicalizes_to.
authority-key      observation.recorded(P) and plan.revised(P) resolve to authority_keys with
                   different consistency classes (AP vs CP) though subject_ref is identical.
id-not-authz       A caller holding a valid subject_ref but lacking project scope/capability
                   gets not_found/forbidden — the id grants no access.
```

## 8. Maxims

```text
Identity is immutable. Meaning evolves through Events.
Equivalence is asserted and proven; it is never guessed.
A Subject identifies what Events are about. It does not grant scope, authority, or truth.
The authority domain is derived from subject + operation + policy — never from the subject alone.
```
