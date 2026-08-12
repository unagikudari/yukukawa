# Kawa Epistemic Claim Model v0.2

Status: Draft, current normative — the epistemic nucleus, realized
Supersedes: `epistemic-claim-model-v0.1.md` (which defined a `Fact` projection; `Fact` is abolished by `specification-v0.5.md` §2.6 — adopted from #85, gated through #97 and its two-round adversarial plan review)
Companions: `event-log-and-replication-v0.1.md` (the envelope these Events ride), `postgresql-physical-schema-v0.3.md` §5/§9/§10 (physical shape), `subject-identity-and-lineage-v0.1.md` (equivalence uses this model's standing)

> **Kawa records claims about reality. It does not declare reality.**

## 1. The nucleus

Three epistemic record kinds and one relation carrier — nothing else:

```text
observation.recorded   what a deterministic collector measured
claim.recorded         what an accountable actor asserts or infers
link.asserted          a typed relation between records (a first-class Event)
```

There is **no Fact**: no durable object and no privileged projection meaning "what Kawa
accepts as true". What v0.1 called Fact splits into (a) the immutable, attributed records
above and (b) **derived standing** (§4) — a rebuildable projection that reports what the
currently-held records support, never what is true.

## 2. Observation — the type gate

An Observation carries `predicate`, **exactly one** typed value
(`value_text | value_number | value_bool | value_time`), and an
`observation_method_class` drawn from a deterministic-collector allowlist
(`command_exit, http_probe, file_digest, api_fetch, metric_read, manual_human`).

**Model inference is not a method class.** An LLM's reading of anything — including of an
Observation — enters as `claim.recorded`. This boundary is enforced twice (payload
validator and SQL CHECK), not requested in prose.

`occurred_at` is payload data (when the thing was seen); Domain order remains the HLC.

Mutable external sources bind by content identity (#98 §2): the optional snapshot tuple
`source_ref + content_digest + fetched_at` (all-or-nothing, `source_revision` optional).
A later fetch that hashes differently is a **new Observation** — never a silent
replacement of an attested basis.

## 3. Relations — asserted, never inferred

A relation is itself a Domain Event (`link.asserted`: `source_ref --relation--> target_ref`),
so it is hash-chained, attributed, append-only, and replicates through the trust gate like
any Event. Vocabulary (v0.5 §5): `supports contradicts based_on reason_for addresses
reviews corrects resolves supersedes` (+ coordination `caused_by satisfies`).

- Self-links are invalid at the root (payload validator + CHECK).
- `event_links` is a **reducer-owned projection** of these Events (dedup key = the triple;
  first asserter kept for attribution). Nothing else writes it; dropping it is repaired by
  replay.
- A link whose `target_ref` is not locally held admits cleanly and sits **unresolved**;
  the arrival of any Event deterministically backfills links targeting it. Unresolved
  links derive nothing.
- Semantic similarity NEVER creates a relation implicitly (v0.5 §5).

## 4. Derived standing — protocol state, not truth

`current_claim_standing` assigns each Claim exactly one of:

```text
grounded_supported | contradicted | contested | superseded | unevaluated
```

by a deterministic, rebuildable, order-independent reduction over the **resolved** link
set. Invariants (fixed by the #97 review rounds):

- **superseded is unconditional**: any resolved `supersedes` edge retires its target,
  regardless of the asserting source's own standing. Un-retiring is a new Claim, never a
  resurrection.
- **grounding requires ground**: `grounded_supported` iff a `supports` path — traversed
  with a visited-set, **pruned at superseded intermediate claims** — terminates at an
  Observation. Claim-only cycles ground nothing.
- **contradiction has no algebra**: `contradicts` never cancels and never flips to
  support; contradicts(contradicts(X)) says nothing about X. Sources must be locally held
  and, if Claims, non-superseded.
- **contested** = grounded AND contradicted. A contradiction-only Claim is `contradicted`
  — which is standing, not falsity.

Standing is one axis (acceptance). Freshness, authority, and trust dimensions are separate
concerns arriving with their own roadmap steps — they are NOT folded into this enum (#85 §2).

Per v0.5 §2.6 this projection is Kawa reporting on **its own records** — protocol state.
Observers construct Situational Awareness from it; Kawa does not.

## 5. Equivalence (subject identity) uses the same pattern

`same_as_candidate` assertions are Claims; equivalence standing
(`clear | conflicted | unknown`) is a projection over them — the v0.1 "equivalence Fact"
renamed to what it always was. Authority-merge (`canonicalizes_to`) remains a governed
CP-plane decision (`subject-identity-and-lineage-v0.1.md` §3).

## 6. Realized mapping (Status Discipline §25: implemented, verified)

```text
kinds        EventKind.LINK_ASSERTED / OBSERVATION_RECORDED / CLAIM_RECORDED
payloads     event_link / event_observation / event_claim   (append-only, sql/0006)
projections  event_links (resolved flag) / current_claim_standing   (rebuildable)
write path   Kawa.assert_link / record_observation / record_claim → emit → reduce
tests        tests/test_epistemic.py — the #97 ten acceptance items + four binding
             constraints, including cross-origin dangling-link backfill through the
             Phase-4C replication gate
```
