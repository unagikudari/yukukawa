# Kawa Operation & Effect Identity v0.8 — the Operation & Effect Identity keystone

Status: **FROZEN — normative (v0.8), frozen 2026-08-11 (earned by attack).** The Operation & Effect Identity keystone (#60): what an effect identity `E` is, how it is node-independent, and how the crash-before-effect / recurrence / supersede firing gate stays exactly-once-safe. This version was **earned across six adversarial rounds**, never by agreement: v0.1–v0.4 *diverged* (each counterexample added a field/state/mechanism); v0.5 established node-independent `E` (both lanes); v0.6 narrowed to two blockers; v0.7 closed the record-replication double-fire; both v0.7 lanes (vendor-local + cross-vendor vendor-A) then **converged on one root defect** — two derived effect-defining inputs (intensional pin-selection; `provably-absent`) were still node-locally choosable while masquerading as "content-addressed"; v0.8 closes it with the single **closure principle (§0.0)** — adding no occurrence-nonce, crash-generation, replication-exception, or retry-epoch (the owner's divergence tripwire stayed clean). v0.8 then **cleared a distinct-vendor two-lane adversarial gate with no surviving safety counterexample in either lane** (vendor-local + cross-vendor vendor-A, both CONFORMING, verdicts on PR #60). The owner freeze-gate reject-question — *could the same causal occurrence acquire different identities solely because it was appended, replicated, compacted, rebuilt, or observed on a different node?* — answers **No** on every path for a well-formed cause under a conforming registry.

**The residual is implementation-gate, not a semantic gap:** §11 lists the executable gates to RUN at the Phase-0 implementation freeze (not to redesign the spec). Two are load-bearing and both lanes flagged them: `G-RD0-pin-selection` (local admission-enrichment that picks the RD0 snapshot for an otherwise-identical pre-resolution cause is node-local resolution and must fail closed) and `G-minter-matrix` (schedule/plan absence-proofs negative-controlled against "last-fired"/"current-ready-node"/mutable-pointer shapes → must yield `unknown-unreachable`/fail-closed, never `provably-absent`). Honest limits L2 (delegated `client` minter), L4 (unobservable-irreversible liveness), and L6 (RD proves node-independence, not semantic freshness) are accepted bounded costs. **Post-freeze:** a Core semantic change takes the full two-lane distinct-vendor adversarial gate again (as v0.5→v0.8 did); the honest condition still binds (if §6 can identify `E1` for supersede without a cause-declared predecessor, the carrier is band-aid and must be removed). The convergence history that produced this freeze is retained deliberately — it is the evidence this freeze was earned by attack, not by agreement.

Lane-drafted then owner-synthesized under the closure principle (§0.0).

**Consumes, never reinvents:** #28 `subject_ref` / `authority_key` / `same_as_candidate`→`canonicalizes_to`; FROZEN ③④ (`E`, `Actuator.CommitToken`, §2.1, §3 consume-once, §5 AuthorityReceipt + `prior_authority_frontier_digest`, §5.1 three-state verifier, §6.1 policy fence, F5 authorization≠physical-exactly-once); the operation registry + drift-CI (#34/#35).

> **Execution identity must distinguish semantic difference without fragmenting equivalent intent.**
> **A counterexample must reveal a missing principle, not earn a new branch.**

---

## 0.0 The closure principle (the root of node-independence)

Content-addressing proves a value is immutable **after** it is chosen; it does **not** prove the *choice* is node-independent. Two derived effect-defining inputs can hide a node-local degree of freedom while calling themselves "content-addressed": (a) *which* durable snapshot resolves an intensional extent, and (b) *whether* a predecessor effect exists.

> **Node-independence is a closure property.** Every input that feeds `E` **or** the firing gate MUST be determined by a **node-independent selector fixed in the forcing cause before any node-local resolution** — not merely content-addressed once some node has chosen it. Any input whose *selection* (not just whose *value*) can differ across nodes for the same pre-resolution cause is an **undischarged** input and MUST fail closed (or fan out extensionally).

RD0 (§3.1) and the per-minter absence-proof contract (§5.1a) are the two instances that close the property. This is a **deepening of the cause-supplied-`occurrence_key` invariant**, not a new mechanism: no nonce, no crash-generation, no replication-exception, no retry-epoch — it **removes** two hidden node-local degrees of freedom.

**The honest boundary (named, not hidden).** *Kawa distinguishes exactly what the cause durably distinguishes.* Two byte-identical intents carrying no distinguishing durable occurrence key **are one occurrence** — the safe default is an idempotent retry, never a silent second effect. To obtain two effects, the cause must supply two occurrence keys. There is no nonce, no wall-clock, and no log position that manufactures distinctness Kawa cannot otherwise justify. No exactly-once illusion is offered anywhere in this spec.

## 0.2 Honest limits (accepted, bounded costs — stated at the gate, not buried)

- **L1 — DISCHARGED (§3.1 incl. RD0).** Resolution is a total replayable function of durable inputs **and** the snapshot selection itself is node-independent (RD0). Undischarged resolvers fail closed.
- **L2 — the `client` minter is a delegated contract Kawa cannot enforce.** `H_OK("client", client_id, idempotency_token)` is node-independent only if the client mints one token per *logical* intent, not per transport attempt; and its `provably-absent` and `occurrence_key` are bound to an authenticated idempotency contract (§5.1a). Kawa cannot compel this. A client that re-mints per attempt defeats its own idempotency (the §6 firing gate still blocks the crash-window double-fire; the retry-merge is lost).
- **L3 — the only partition-sensitive step on the firing path** is the `present`-carrier verification of `effect_standing(E_prev)=absent` (§6). It resolves by querying ③④'s durable receipt store; under partition it may be unreachable → `INCOMPLETE` (③④ §5.1) → blocks. A deliberate liveness cost paid to avoid a double-fire. The carrier *read* is local and total, adding no partition cost.
- **L4 — an unobservable irreversible effect with no native key has no automated second-effect liveness.** When `effect_standing(E_prev)` is permanently unverifiable and the target exposes no adequate native idempotency key, forcing a second effect stays `terminal_unknown` (③④ F5): the new `E` unfired, escalates to policy. A bounded liveness cost, chosen over a double-fire.
- **L5 — `occurrence_key_projection` totality is proved by failing-to-refute, not universally.** Asserted total over the verb's **enumerated closed** `durable_cause_field_set` via drift-CI failing to refute, not a universal proof. Fixtures MUST be **real/adversarial, never self-authored** (self-authored fixtures green the author's own misunderstanding — a missing case then appears as "the test passes"). Now expanded to a **minter matrix** (client/schedule/plan), §11.
- **L6 — RD proves node-independence, not semantic freshness.** Pinning targets the extent as-of authorization; a verb needing fire-time-live membership must be classified `fanout-only` (§3.2). "Safely-pinnable vs must-fan-out" is a registry judgment; a mis-classification yields a semantically-stale set, caught by RD3 only if staleness manifests as cross-node divergence.

## 1. Membership rule + structure = rule

`E` is the digest of the **registry-declared, semantically-canonicalized, effect-defining projection** of the operation. A field is in the projection **iff** changing it produces a *distinct semantic side effect* — tested in **both** directions:

```text
false merge:  different side effects collapse into one E   (→ a real effect is wrongly blocked/deduped)
false split:  equivalent intents fragment into many E      (→ an irreversible effect runs twice)
```

Rather than a fixed slot list, the **operation registry declares, per operation, its effect-defining projection**: which axes and which params are effect-semantic. New operations extend the registry; they never edit Core. Existing-verb projections are **immutable** (drift-CI-enforced, §3/§2 R3).

## 2. The occurrence key + occurrence_key_projection

`occurrence_key` is an opaque content address, deterministically derived from the **cause's** durably-distinguishing coordinate, containing **no** node id, log position, wall-clock, or nonce.

**Who mints it — the cause, never Kawa.** The registry declares per verb an `occurrence_key_projection`: which durable cause-fields constitute this verb's occurrence identity. The key is the content address over that projection:

```text
occurrence_key_projection (per verb, registry-declared, drift-CI-frozen for existing verbs):
  = ( occurrence_minter_tag,        -- client | schedule | plan
      durable_cause_field_set,      -- the EXACT durable cause fields constituting occurrence identity — enumerated, closed
      occurrence_coordinate_fn )    -- a TOTAL, REPLAYABLE function: durable_cause_field_set ↦ occurrence_coordinate

occurrence_key = H_OK( occurrence_minter_tag, JCS( occurrence_coordinate_fn(durable_cause_field_set) ) )
```

- **R1 — durable-bytes-only, total, replayable.** `occurrence_coordinate_fn`'s inputs are exclusively durable cause bytes reachable identically on every node; total (defined for every well-formed cause) and replayable (same durable bytes → byte-identical coordinate on any node, any time). Excluded (non-exhaustive): node-local profile (timezone, locale, numeric-rendering precision), wall-clock-at-fire, arrival order, compaction-sensitive position. For a schedule the coordinate is the schedule **definition's content-version + the definition-assigned logical tick index** — never a formatted timestamp string, never a node-local rendering:
  ```text
  schedule occurrence  occurrence_coordinate = ( schedule_definition_content_version, logical_tick_index )
  ```
  If the definition's bytes change, its content-version changes → a new schedule identity (a new schedule, not a re-rendering of the old).
- **R2 — drift-CI checks BOTH directions.** Anti-false-merge: a coordinate coarser than the verb's cadence collides two occurrences → fails. Anti-false-split: two replays of `occurrence_coordinate_fn` over the same durable bytes must be byte-identical, and the coordinate must encode no resolution finer than the definition's logical tick; drift-CI fuzzes replay across simulated node profiles/clocks/locales/arrival-orders and asserts identical output — an over-fine (variable-across-renderings) coordinate is rejected exactly as a coarse one is.
- **R3 — `registry_version`-immutable for existing verbs, BOTH directions.** An existing verb's `occurrence_key_projection` (all three components) is drift-CI-frozen; a bump MUST NOT make it finer (re-mint → false split → double-fire) or coarser (→ false merge → lost effect). `registry_version` may *select* canonicalization RULES only for (a) new verbs and (b) `intent_projection` canonicalization; it does not reopen on `occurrence_key_projection`. Changing a projection is minting a **new verb**, never re-versioning an old one.
- **R4 — fail-closed on omission.** A verb with an irreversible effect and no declared `occurrence_key_projection` fails closed; it may not FIRE.

Three canonical minters (the registry names one per verb; a missing declaration **fails closed for irreversible effects**):

```text
client idempotency   H_OK("client",   client_id, idempotency_token)
                     -- token minted ONCE per logical intent by the client, not per transport attempt
schedule occurrence  H_OK("schedule", schedule_ref, occurrence_coordinate)
                     -- occurrence_coordinate = the schedule's LOGICAL tick (the instant/index the
                     --   schedule DEFINITION assigns), never wall-clock-at-fire
plan node            H_OK("plan",     plan_ref, plan_node_ref)
```

**How retry re-derives the same key.** A retry replays the *same cause projection*: the client resends the same `idempotency_token`; the scheduler re-fires the same missed logical tick; the plan re-executes the same node. Identical projection → identical `occurrence_key`. Retry is therefore not a special case — it is byte-identity of the cause.

**Node-independence.** Because the key is a content address over cause-state with no node/position/clock/nonce term, two partitioned nodes handling the same logical occurrence derive the **identical** `occurrence_key`. Compaction cannot move it — it is content, not position.

## 3. The `E` derivation + the `H_E` obligation

```text
intent_projection = ( semantic_operation,          -- registered verb; granularity is a GOVERNED
                                                    --   registry decision, drift-CI-checked — NOT an
                                                    --   iff over projection shape (disk.wipe ≠ disk.format)
                      canonical_target_set,         -- complete effect targets, each resolved to a
                                                    --   canonical external_effect_ref; registry declares
                                                    --   the target domain/predicate; actuator FAILS CLOSED
                                                    --   if a realized target falls outside it; order-
                                                    --   independent unless the verb declares ordering-semantic
                      resolved_scope,               -- resolved extent; compared via the total scope algebra
                      canonical_effect_params )      -- intent params only (no realized output/nonces);
                                                    --   semantically canonicalized: typed coercion → Unicode
                                                    --   NFC → default materialization → THEN JCS

E = H_E( intent_projection, occurrence_key )
```

**Excluded from `E`, with reason:**

| Excluded | Why |
|---|---|
| `admission_ref` / any log position | The event substrate provides no unique+scoped+compaction-stable coordinate; embedding it false-splits retries under compaction. Dissolved entirely. |
| `policy_digest` | Policy is a ③④ **fence** input (§6.1), never an identity input. In `E`, a policy bump re-mints `E` and re-fires a consume-once effect. |
| `registry_version` | May *select* canonicalization RULES for **new** verbs; but existing-verb projections are immutable (drift-CI), so a bump **cannot** re-mint `E` for an existing effect. |
| any mutable admission/standing field, nonce, wall-clock | Non-deterministic → false-splits retries and re-opens the exactly-once illusion. |
| the authorization frontier | Ordering is not identity (§5). `E` is order-free; ③④'s consume-once guard keys on `E`. |

**The three cases (registry `occurrence_key_projection` is the sole distinctness source):**

```text
RETRY      same occurrence_key, same intent_projection  → same E        (idempotent)
RECURRENCE new occurrence_key,  same intent_projection  → new E′        (distinct effect)
CONCURRENT distinct occurrence_keys                     → distinct E     (distinct effects)
```

**The `H_E` obligation (scoped).** `H_E` is a shared primitive spanning this slab and the ③④ actuator; ③④ §2.1 is the coordination-avoidance classification table — it names the `Actuator.CommitToken` invariant over `E` but **exports no hash** and defines no derivation of `E`. Conformance is therefore **full descriptor/input equality**, not equal output on a happy-path corpus: both sides derive identical `E` from identical `(intent_projection, occurrence_key)` **including every canonicalization edge case** — Unicode NFC, float/numeric rendering, and **default materialization** (omitted-vs-explicit default must canonicalize identically). The §11 corpus MUST include discriminating negative controls at each edge.

**Recompute-vs-consume pinned:** `E` is derived **once**, by the operation layer; ③④ **consumes it as an opaque value** (③④ §2.1 exports no hash — nothing to recompute from). No component re-derives `E` independently; any re-verification MUST invoke the **single shared `H_E` + canonicalization implementation** (a shared library, not a second reimplementation validated by test). Two-derivations-plus-a-test is strictly weaker than one shared implementation (the test only samples the input space, L5). The conformance test is a §11 gate item guarding the shared boundary. Until it passes over the full edge-case corpus, cross-slab `E` agreement is unproven.

## 3.1 Resolver-determinism for intensional targets (discharges L1)

For **extensionally-resolved** verbs (targets literal in the cause) node-independence holds trivially. For **intensionally-targeted** verbs (predicate/glob targets, scope over live membership — every fleet fan-out), resolving against node-local state yields a different extent per node → different `intent_projection` → different `E` → **false-split → double-fire**. Resolution gets the full closure treatment:

- **RD0 — node-independent pin-SELECTION.** The snapshot/roster input identity fed to `resolve()` MUST be either:
  - (a) **explicitly named in the forcing cause** before any node-local resolution, or
  - (b) derived from a **single authoritative, total, replayable lineage selector** unique for the occurrence (e.g. a policy-pinned roster-version ref governed as a ③④-style `configuration_digest`).

  If two valid snapshot refs can be selected for the same pre-resolution cause, the resolver is **undischarged** and MUST fail closed or fan out extensionally. (Closes the S1={a,b} / S2={a,b,c} partition split: content-addressing S1 and S2 individually does not make the *choice* between them node-independent.) The durable snapshot/roster artifact named by RD0 MUST actually exist in the substrate as a content-addressed versioned digest — its existence is a §11 gate item, not an assumption.
- **RD1 — total, replayable function of durable inputs.** `resolved_extent = resolve(target_predicate, durable_inputs)` where `durable_inputs` are the RD0-selected content-addressed bytes, reachable identically on every node — never a node-local live query.
- **RD2 — the resolved extent is content-addressed and PINNED into the projection, resolved ONCE, never re-resolved per node.** A live membership change after pinning does not change `E` (correct: the op targets the extent as-of authorization). Pinning is part of the durable cause projection.
- **RD3 — drift-CI fuzzes resolution across simulated node state** (membership/roster/clock/arrival-order) and asserts a byte-identical pinned extent — **including fuzzing RD0 selection**: two selectable snapshots for one pre-resolution cause MUST be rejected, not silently pinned by whichever node resolved first.
- **RD4 — fail closed on an undischarged resolver.** A verb whose resolver cannot pass RD0–RD3 **may not FIRE**: it blocks until discharged, or is re-expressed as **extensional fan-out** (one sub-operation per resolved member, each a literal target with its own `occurrence_key` and its own `E` → ③④ dedups per-`E`; divergent rosters yield at most a missed/extra member — a completeness gap — **never a double-fire**). "The roster is probably the same everywhere" is not a discharge.

## 3.2 Registry completeness gate + closed minter-derivation contract

**Resolver declaration.** Every verb whose target set is **not literal** MUST declare `resolver_kind ∈ { extensional_already | intensional_RD0_RD3_discharged | extensional_fanout_only }`. An undeclared intensional resolver is a **schema/gate failure** (fails closed), not a default-permit.

**Closed minter-derivation contract.** Each minter (client/schedule/plan) declares, in the governed registry, functions **total over content-addressed cause bytes**:

```text
durable_cause_field_set
occurrence_coordinate_fn          -- → occurrence_key
predecessor_declaration_fn        -- → present(E_prev) | provably-absent | unknown-unreachable | INVALID
allowed_absence_proof_fn          -- what makes provably-absent PROVABLE for this minter (§5.1a)
resolver_pin_selection_fn         -- RD0 selector, if targets are intensional
```

**Any** dependency on local-projection absence, latest-live-membership, wall-clock, arrival-order, or a mutable pointer is an **undischarged** minter/resolver and **fails closed**. Drift-CI checks each function's domain ⊆ content-addressed cause bytes.

## 4. The OccurrenceRecord (a name, never an identity input)

An OccurrenceRecord is the durable **binding** of `(occurrence_key, E, work_ref)` for fetch, reconcile, and audit. **Nothing on it feeds `E`, and nothing on it authorizes firing.** It is minted once per `occurrence_key` and re-read (never re-minted) by retries.

```text
OccurrenceRecord {
  occurrence_key                 -- identity input to E (content address from the cause)
  E                              -- = H_E(intent_projection, occurrence_key)
  intent_projection_digest       -- audit binding of the projection that produced E
  resolved_extent_digest         -- the pinned intensional extent (§3.1), for audit/reconcile
  work_ref
  requested_by_cause_ref         -- provenance of the cause
  follows_prior_effect           -- PERSISTED copy of the CAUSE-CARRIED predecessor declaration
                                 --   (four-valued, §5.1). NOT an input to E. NOT the source the §6
                                 --   firing gate reads (that is the forcing cause's bytes).
  -- MUTABLE / POLICY — for fetch, the ③④ fence, and reconcile; NEVER fed to E, NEVER an authority input:
  policy_digest_at_admission     -- pinned for the ③④ §6.1 fence, not for identity
  registry_version_at_admission
  reconcile_status?
}
```

- **(a) The record persists the carrier and `resolved_extent_digest`; it authorizes nothing.** §6 reads `E1` from the forcing cause's bytes; whether the record replicated to the firing node is **irrelevant to firing**.
- **(b) `resolved_extent_digest` is audit-only:** a §11 conformance check MUST show that mutating/removing it changes **neither** `E` **nor** the firing decision, while mutating the **cause-pinned** extent **does** change `E`. (Guards against the shadow silently re-entering the firing path.)
- **(c) `occurrence_standing` is DELETED.** Every value a one-word occurrence status could take (`named/authorized/fired/reconciled/aliased/rejected`) is a projection of the four standing axes (§7) — axis-conflation §7 forbids. Any one-word occurrence status is a **strictly-derived read-model view** over the four axes + occurrence relations, carrying no independent authority and never a stored field, checked by **semantics, not field-name**.

**Retry-dedup decoupled from record fetch.** A retry carries the same cause projection → re-derives the same `occurrence_key` and `intent_projection` (over the *pinned* extent) **locally** → derives `E1` locally. Retry safety keys on **③④'s consume-once `E`-guard**, not on fetching the record. The record fetch is convenience for `work_ref`/reconcile/audit only; its unavailability degrades reconcile richness but MUST NOT block retry dedup/progress.

## 5. Three homes for three concerns — the recurrence-lineage carrier

③④'s frontier is **configuration** lineage: `prior_authority_receipt_digest` "binds configuration lineage" (③④ §5 verbatim) — it answers *which configuration authorized this, descending from which prior configuration* — NOT *which prior effect this recurrence supersedes/follows*. Two recurrences under the same unchanged configuration share identical config lineage yet are distinct effect occurrences in a recurrence order — so config-lineage structurally cannot express effect-recurrence order. §6's crash gate ("`FIRE(E2)` must know `E1`") therefore needs a **named source** for `E2→E1` that ③④ does not provide.

### 5.1 The carrier — four dispositions, cause-reconstructible

```text
follows_prior_effect : { present(E_prev) | provably-absent | unknown-unreachable | INVALID }
```

A **predecessor declaration** carried by the recurrence's **cause** (the same trust boundary that mints `occurrence_key`), naming at most one prior effect `E_prev`. Its content is the `E_prev` digest — a node-independent content address reconstructible byte-identically on every node from the cause bytes. The record merely *persists* it; the §6 firing gate reads it from the cause, never from the record. It is **one predecessor pointer, not a set** (not a frozen-at-mint grow-only `generation_basis`), with three normative properties: **(a)** NOT an input to `E` (`E = H_E(intent_projection, occurrence_key)` — the carrier appears nowhere; `E` stays order-free); **(b)** the named source of truth for effect-recurrence order — answered here and only here, not by ③④ (config lineage), not by `occurrence_key` (identity); **(c)** what §6 Window B reads to identify `E1`.

**Four dispositions** (mirroring ③④ §5.1's VALID/INVALID/INCOMPLETE):

- **`present(E_prev)`** — the cause positively names a **well-formed, resolvable** predecessor; §6's verification precondition applies. **Only supersede-intent causes may carry `present`** (§5.1b); an ordinary recurrence never does.
- **`provably-absent`** — the cause carries an **affirmative, PROVABLE** no-predecessor declaration (see §5.1a for what "provable" means per minter). This, and only this, is "first occurrence" — it fires freely because the cause's own durable declaration says so, node-independently. An ordinary recurrence tick **is** `provably-absent` (an independent first occurrence), not a `present(E_prev)` link.
- **`unknown-unreachable`** — the declaration **cannot be determined** (silent/unpopulated field, or an inference relying on a possibly-unreplicated local record/projection). *Absence-of-a-declaration is NOT declaration-of-absence.* For a **supersede-intent** cause this **FAILS CLOSED (blocks, L3)** — MUST NOT degrade to "first occurrence." (INCOMPLETE-as-first-class: unknown is neither "no predecessor" nor authority to fire.)
- **`INVALID`** — the carrier is **decidably malformed from the cause bytes alone** — a syntactically-garbage digest or an ill-typed `E_prev` (wrong length/encoding, not a well-formed `E` value). This is **rejected** at admission (mirrors ③④ INVALID: the declaration is provably not well-formed), **not** treated as `present(garbage)` to block forever. Rejection is a fail-closed reject, distinct from the fail-closed *block* of `unknown-unreachable`. **INVALID is decided by well-formedness, never by resolvability/existence:** a *well-formed* `E_prev` that merely is not present on the local node is **`present(E_prev)` → §6 verification → blocks under partition (L3)**, never INVALID — the firing gate reads the cause, and "I can't see that `E` yet" is a partition condition, not malformity.

Determining the state is a **local, total read of the cause bytes** — never the network. `unknown-unreachable` arises only from a silent/undeterminable declaration; `INVALID` from a bytes-decidable malformity; neither from a partition. (The only partition-sensitive step is the `present`-path verification — L3.)

### 5.1a `provably-absent` proof rules per minter

`provably-absent` has operational force (it fires freely), so it MUST be a **proof**, never "I did not find a predecessor locally." Per minter (`allowed_absence_proof_fn`, §3.2):

- **client:** an explicit, **authenticated** first-occurrence assertion under the client idempotency contract (L2-delegated). Never inferred from Kawa-side record absence.
- **schedule:** **derivable from the schedule-definition content-version + logical tick index + the recurrence-lineage rule** — content-addressed, so a crashed/partitioned node re-derives the *same* answer. **Never** from "last fired tick" read off a local projection. If the schedule bytes do not deterministically assign the tick index and predecessor relation, the minter is undischarged → `unknown-unreachable`.
- **plan:** **derivable from the content-addressed immutable plan revision (`plan_ref`) + a `plan_node_ref` stable within that revision** + the node-predecessor relation. **Never** from a mutable plan pointer, workspace branch, or local planner projection.

**Partition rule (the closure core):** whenever a minter is partitioned from predecessor state and cannot *derive* the proof, it MUST emit `present(E_prev)` (if the durable bytes name one) or `unknown-unreachable` — **never** `provably-absent`. A schema/static check forbids `provably-absent` from being the **default** value for supersede-capable causes; silence maps to `unknown-unreachable`.

### 5.1b Carrier scope: supersede-intent only

The carrier's *predecessor-link* (`present(E_prev)`) exists **for supersede-intent, and only supersede-intent.** The band-aid test binds: §6 cannot identify `E1` without a cause-declared predecessor **for supersede** (③④ config-lineage can't distinguish two recurrences under unchanged config; `occurrence_key` is identity not order; the record is a possibly-unreplicated shadow) — so the carrier is **necessary** there. It is **removable** for ordinary recurrence, which needs no predecessor link (`provably-absent`).

**Conflict handling (#28):** an AP-minted conflicting carrier *record* is a **reconciliation signal** (`conflicting/reconciliation-pending`), never authority-relevant — the firing gate reads the cause-carried `E_prev`, so a record conflict is a reconcile flag, not an identity or firing input. Bound at OccurrenceRecord mint; immutable after mint, like `E`; a *conflicting* carrier is a detected conflict surfaced via #28 reconciliation, never a silent overwrite.

### 5.2 Three-homes statement

| Concern | Home | Question |
|---|---|---|
| **Identity** | `occurrence_key` → `E` (§2–3) | *Is this the same occurrence?* |
| **Configuration lineage** | ③④ `prior_authority_receipt_digest` / `prior_authority_frontier_digest` (③④ §5) | *Which configuration authorized this, from which prior configuration?* |
| **Effect-recurrence order** | `follows_prior_effect` (§5.1) | *Which prior effect does this occurrence supersede/follow?* |

Orthogonal, not layered SoTs: no home is derivable from another. ③④ still owns the consume-once guard (keyed on `E`) and config lineage — this slab removes nothing from ③④ and duplicates nothing it holds.

### 5.3 No SoT-creep vs ③④

The carrier homes a concern ③④ never had a home for — a different concern (effect vs authority/config descent, proven by the same-configuration counterexample); consume-once stays ③④'s, keyed on `E`; the carrier is not fed to `E` (so it cannot re-mint `E`, re-open the escape, or give one occurrence two identities).

## 6. The crash-before-effect gate

`execution_unknown` is **not** rollback (③④ §3: rollback ≠ non-execution — once `A` was a valid authorization, the consume-once invariant for `E` is spent even if the world later compensates; a fresh attempt uses a new `E′` or a valid never-exercisable proof for `A`). The gate satisfies §3 **without deadlock and without double-fire** by splitting two crash windows onto the epistemic axis (§7) and gating the fresh-firing precondition.

**FIRE precondition (global).** A fire path MUST have the **complete content-addressed cause bytes locally available**. Firing from `E`, `work_ref`, a bare `cause_ref` whose bytes are unavailable, or the OccurrenceRecord shadow is forbidden (fails closed). A `cause_ref` is sufficient only once dereferenced to local content-addressed bytes.

**Window A — crash *mid authorization-append* (`epistemic = authorization_existence_unknown`).** ③④'s guard passed but `AuthorizationIssued(E1)` may not be durable. Kawa MUST NOT mint a fresh `E2` here: if `E1` *is* durable and we treat it as unauthorized, a fresh fire double-fires. Resolution is forced to query ③④'s durable receipt store for `E1`:

- receipt VALID → `E1` is authorized; transition to Window B (`execution_unknown`);
- receipt provably absent → safe to re-drive the *same* `occurrence_key` → same `E1`; re-authorization is idempotent at ③④'s guard (keys on `E`);
- receipt INCOMPLETE → stays absorbing w.r.t. safety (③④ §5.1); block, never guess.

**Window B — crash *after authorization, before Result* (`epistemic = execution_unknown`, `effect = unknown`, `consumption = consumed`).** `AuthorizationIssued(E1)` is durable and spent. To fire again the cause must supply a **new** `occurrence_key` → new `E2` (③④ §3: a fresh attempt uses a new `E′`). Minting `E2` is unconditional and immediate — **no deadlock**, because `occurrence_key` is cause-supplied and never waits on a frontier to advance. The predecessor `E1` is read from `E2`'s **cause-carried** `follows_prior_effect` (§5.1), reconstructible on the firing node independent of record replication. The **actuator commit of `E2` for a non-idempotent irreversible effect** is then gated:

```text
read follows_prior_effect from the E2 CAUSE (local, total, over complete cause bytes):
  provably-absent      → first occurrence → FIRE(E2) freely (two distinct effects the cause PROVABLY asked for — honest boundary, not a double-fire)
  present(E1)          → FIRE(E2) iff VERIFIED effect_standing(E1)=absent OR the target exposes a native idempotency key of equal/finer granularity;
                         else E2 is NAMED but UNFIRED (L4 terminal_unknown). Verification is L3-partition-sensitive → under partition BLOCKS. No double-fire.
  unknown-unreachable  → FAIL CLOSED (block). E2 does NOT fire. (MUST NOT read as first occurrence.)
  INVALID              → REJECT the cause at admission (not a block; the declaration is not well-formed).
```

If `effect_standing(E1)` is unverifiable **and** no adequate native key exists, the effect is `terminal_unknown` (③④ F5): `E2` remains unfired and escalates to policy — a bounded liveness cost for one non-idempotent irreversible effect, never a double-fire and never a whole-domain deadlock. A retry (same `occurrence_key` → `E1`) remains idempotent at ③④'s `E`-guard throughout, reads no carrier, never blocks on a record; only *forcing a second effect* reads the carrier and pays the verification precondition.

## 7. Standing axes — three orthogonal + a separate epistemic axis

```text
authority_standing   : valid | rejected | cancelled | revoked | expired | superseded | invalidated
effect_standing      : absent | partial | occurred | unknown
consumption_standing : unconsumed | consumed | indeterminate
epistemic_standing   : evidence_complete | verification_pending
                       | authorization_existence_unknown   -- Window A (guard passed, receipt durability unconfirmed)
                       | execution_unknown                 -- Window B (transient, crash-before-Result)
                       | terminal_unknown                  -- verifiability permanently exhausted (③④ F5)
                       | conflicting_evidence
```

All four axes are orthogonal and composable; **epistemic is never an input to `E`** — so `E` stays deterministic and epistemic state is never conflated into identity/authority/effect/consumption. The crash-after-token / crash-before-observation window is the tuple `(authority=valid, consumption=consumed, effect=unknown, epistemic=execution_unknown)` — representable **without changing `E`, reopening consumption, or downgrading authorization**. `authorization_existence_unknown` and `execution_unknown` are distinct values; `execution_unknown` (transient) and `terminal_unknown` (unverifiable, ③④ F5) stay distinct.

**Normative deletion:** there is no `occurrence_standing` axis and no `occurrence_standing` field. Any single-word "occurrence status" is a strictly-derived read-model view over the four axes + occurrence relations, carrying no independent authority and feeding no decision — never a fifth stored enum.

**Relations, total, de-aliased** — named labels ("retry", "recurrence", "concurrent") are **derived views** over these relations, never a Core priority enum:

```text
occurrence_relation : same_occurrence | distinct_occurrence | aliased_occurrence | unresolved
execution_relation  : same_E | different_E | unresolved_equivalence
scope_relation      : equal | disjoint | subset | superset | overlapping_incomparable | unknown
outcome_relation    : agree | divergent | unknown
```

Policy is a ③④ fence input (§6.1), never an identity input — the OccurrenceRecord pins `policy_digest_at_admission` for the fence to re-check at commit (reject default; digest-pinned bounded grace optional). A `registry_version` bump selects canonicalization rules for new verbs only; existing-verb projections are frozen by drift-CI, so no bump can re-mint an existing `E`.

## 8. Work identity, Result fidelity, retry-as-properties, ③④ boundary

- **Work identity (#1):** `work_identity = H(plan_ref, verb, canonical_target_set, resolved_scope)` — a content address of intent, **recomputed-and-verified**, never read as authority from a projection (else it is a hidden second SoT). `resolved_scope` is the resolved extent, so a roster change behind a late-bound scope name is a *content* change (supersession), not a silent same-Work re-execution. A Plan revision that changes the projection mints/supersedes Work; a rationale-only change preserves it.
- **Result contract + fidelity (#2):** evidence fields (`work_ref, verb, canonical_target_set, resolved_scope, effect_identity?, execution_basis, runtime_ref/node_ref, authority_standing, effect_standing, consumption_standing, epistemic_standing, evidence_digest`) — correlatable without runtime conversation state; a Result is **evidence**, never itself Fact or Plan-satisfaction. *Transport success ≠ Result completeness:* the producer emits a **contract-derived completeness assertion** (expected size / element count / terminal marker — not producer-self-reported, catching a producer that truncated then self-digested) and signs `(work_ref, E, content_digest)` so an artifact reference cannot be substituted with a valid-but-unbound digest. A Result is `incomplete` until Kawa holds the full payload or a verifiable, bound artifact reference.
- **Retry-as-properties (#6)** (not classes): `supports_native_idempotency_key · native_key_granularity ∈ {equal,coarser,finer,none} · can_observe_effect · verification_strength · reversibility · external_target_supports_query` → reducer → retry / verification / compensation path. `execution_unknown` never blind-retries a side-effecting op; it mints verification Work, which resolves to `effect_standing ∈ {absent, partial, occurred, unknown}`, and a genuinely unverifiable effect has an explicit **terminal-unknown** outcome (honest, ③④ F5), not a false "resolvable." Consume-once is **forbidden across a native-key granularity mismatch** (coarser/none) unless a reconciliation is specified — this is what §6's native-key branch relies on.
- **③④ boundary:** the same `E` object; a shared `H_E` primitive whose cross-slab equality is discharged by the §11 conformance test (③④ §2.1 exports no hash to import). **③④ §2.1 is NOT demoted** — it is the frozen classification of consistency primitives; the operation registry maps application verbs onto them, and drift-CI checks the mapping codomain ⊆ the frozen classes. F5 native-key **granularity relation** is recorded per adapter (`equal|coarser|finer|none`). This slab specifies only how the shared `E` is *derived* and how standing composes with it — it does not reopen ③④.

## 9. Walk-throughs

**Retry.** Client resends the same `idempotency_token` → same `occurrence_key` → same `intent_projection` → **same `E`**. ③④'s consume-once guard sees `E` already present: no new authorization, the existing Result is returned. Not a fresh attempt (same `E`). **No double-fire** (guard dedups on `E`); **no deadlock** (immediate).

**Recurrence.** Next schedule tick → the cause replays `occurrence_coordinate_fn` over the same durable definition bytes with the next `logical_tick_index` → new `occurrence_key` → **new `E′`** (order-free). An ordinary recurrence tick is an **independent first occurrence** — its `follows_prior_effect` is `provably-absent`, not a `present(E_prev)` predecessor link (a predecessor link would silently stop every recurring schedule at §6). ③④ authorizes `E′` fresh and enforces consume-once on `E′`; its AuthorityReceipt records `E′`'s **configuration** lineage (unchanged config ⇒ same config-lineage digests as the prior tick — correct, and exactly why config-lineage cannot carry recurrence order). §3 satisfied (fresh attempt, new `E′`); **no false-merge** (distinct keys → distinct `E`); no duplicate SoT (three orthogonal homes, §5.2).

**Concurrent distinct effects.** Two causes supply two distinct `occurrence_keys` → `E1 ≠ E2`; both authorize and fire independently. Had they been one logical occurrence, the cause would have supplied one key (→ same `E`, dedup) — the honest boundary decides. **No false-merge, no false-split.**

**Crash-before-effect.** `AuthorizationIssued(E1)` durable+spent, actuator dispatched, crash before Result → `(authority=valid, consumption=consumed, effect=unknown, epistemic=execution_unknown)` — representable without touching `E`, reopening consumption, or downgrading authority. Retry (same key → `E1`) is idempotent. To force a second effect the cause mints a new key → `E2` (immediate, **no deadlock**); `FIRE(E2)` reads the cause-carried carrier: `present(E1)` waits on VERIFIED `effect_standing(E1)=absent` or an `equal|finer` native key (**no double-fire**); `provably-absent` fires (two effects the cause provably asked for); `unknown-unreachable` fails closed; `INVALID` rejects. The mid-append variant lands in `authorization_existence_unknown` and resolves by querying ③④'s receipt for `E1` before minting anything (Window A).

**Offline / partition.** A partitioned node may append an OccurrenceRecord locally (AP: NAME) but **may not FIRE** an irreversible consume-once effect (that needs a CP `Actuator.CommitToken`). Two partitioned nodes handling the *same* logical occurrence derive the **same** `E` (node-independent content address) → on heal the ③④ CP fence dedups with **no coordination**. Genuinely-distinct causes → distinct `E`, both preserved and reconciled via #28 `same_as_candidate → canonicalizes_to` (singleton+equivalent → alias/reject one *before* authorization; additive → keep both; already-fired-twice non-reconcilable → surface divergence, never pretend exactly-once).

## 10. Self-attack — both directions

**False-merge (two distinct effects collapse to one `E`; an effect is lost):**

1. *Cause reuses one `occurrence_key` for genuinely-distinct intents.* Merges — **by design** (the honest boundary). Fundamental to a nonce-free deterministic `E`. Mitigated by registry `occurrence_key_projection` discipline + an authenticated cause; not eliminable at this layer.
2. *Coarse `occurrence_coordinate` collides two schedule ticks* (a month-bucket coarser than the fire cadence). Mitigated: drift-CI requires the declared coordinate granularity to be **finer than the verb's recurrence cadence**; a coarser declaration fails the gate (R2).
3. *Same key across a partition → same `E`.* This is the **intended** retry/dedup semantics (one logical occurrence), not a false-merge.

**False-split (one logical effect gets two `E`; double-fire):**

1. *Client mints a fresh idempotency token per transport attempt.* Would split. Closed by contract: the `occurrence_key` **is** the retry key in the API; a token is minted once per logical intent. A client that violates this defeats its own idempotency — but the §6 firing gate still prevents the crash-window double-fire (L2).
2. *Compaction/replication rewrites a position.* Cannot split — `E` contains **no** position; `occurrence_key` is a content address, compaction-stable.
3. *`registry_version` bump re-mints `E`.* Closed: existing-verb projections are drift-CI-immutable, both directions (R3); a bump only introduces new verbs.
4. *`policy_digest` bump re-mints `E`.* Closed: policy is a fence input, never in `E`.
5. *Crash-before-effect fresh attempt.* `E2` fires only past VERIFIED `effect_standing(E1)=absent` or an `equal|finer` native key — no double-fire.
6. *Non-deterministic canonicalization* (float/Unicode/default drift) splits two identical intents. Mitigated by the shared semantic pipeline (typed coercion → NFC → default materialization → JCS) and the `H_E` conformance test between operation layer and actuator.
7. *Intensional resolver picks a different snapshot per node* (S1={a,b}/S2={a,b,c}). Closed by RD0: pin-selection must be cause-fixed or a single authoritative lineage selector; two selectable snapshots for one pre-resolution cause → undischarged → fail closed / fan out.
8. *A minter emits `provably-absent` from local record absence under partition.* Closed by §5.1a: `provably-absent` is a proof from content-addressed cause bytes; a partitioned minter must emit `present(E_prev)` or `unknown-unreachable`, never `provably-absent`; silence maps to `unknown-unreachable`.

**Closure-principle self-checks:**

- *Does RD0 just relocate the choice to "who names the snapshot"?* No — RD0 requires the selector to be in the cause before node-local resolution, or a single total replayable lineage selector; if two selectors are possible the resolver is undischarged → fail closed. **Residual:** if a verb's authoritative roster selector is itself only eventually-consistent, it is `fanout-only` (L6) — RD proves node-independence, not semantic freshness.
- *Does the closure principle collapse legitimate dynamic-target ops?* No — dynamic-membership ops are `extensional_fanout_only`: each member is a literal sub-op with its own `E`; divergent rosters cost completeness (missed/extra member), never a double-fire. The principle refuses only *silent* node-local selection, not dynamism.
- *Does per-minter absence-proof re-introduce a special case per minter?* No — it is one contract (`allowed_absence_proof_fn`) instantiated three times over content-addressed bytes; it **removes** the hidden "I didn't find it locally" shortcut rather than adding a branch. Client remains L2-delegated (honest limit); schedule/plan are Kawa-derivable and **cannot lie** (the two minters Kawa controls are provably safe).
- *INVALID vs unknown-unreachable — a fourth state bloat?* No — they are the two distinct ③④ failure modes already frozen (INVALID = proven-not-wellformed → reject; INCOMPLETE = can't-determine → block). Folding them would re-conflate what ③④ keeps separate.

**Residual risks (stated honestly):**

- **The distinguishability boundary is the cause's durable recording.** A reused/forged occurrence key merges; a client that re-mints keys per attempt splits; a cause that lies (`provably-absent` when a predecessor exists) forces a genuine second effect. Both are the cause's delegated contract (L2), surfaced as a #28-reconcilable record conflict, mitigated but not eliminable here.
- **Verification-precondition availability.** When `effect_standing` is unverifiable and no adequate native key exists, a non-idempotent irreversible effect stays *unfired* (`terminal_unknown`, policy escalation, L4) — a bounded liveness cost, chosen over double-fire.
- **Registry discipline is load-bearing.** Every irreversible verb MUST declare `occurrence_key_projection` (source + coordinate granularity), native-key granularity, `resolver_kind`, and its closed minter-derivation functions; missing → fail-closed.
- **Semantic freshness is not proven (L6).** RD proves node-independence of the pinned extent, not that the extent is fresh at fire time; a verb needing fire-time-live membership must be `fanout-only`.

**No new substrate primitive required.** `E` depends on no log-position coordinate. Occurrence-distinctness is a cause-supplied content address (compaction-stable by construction); ordering/consume-once is ③④'s AuthorityReceipt chain + the cause-carried carrier; durability of the OccurrenceRecord uses ordinary append (its identity field is content, not position). The only substrate obligations are ones the frozen slabs already provide: content-addressed hashing, ③④'s consume-once guard, and #28's offline-mintable opaque refs.

## 11. Acceptance gate (the concrete gates to RUN)

**Primary reject-question (owner, standing principle):**

> **Could the same causal occurrence acquire different identities solely because it was appended, replicated, compacted, rebuilt, or observed on a different Node? If yes → REJECT.**

**Convergence-vs-divergence test (owner):** a fix must resolve counterexamples by *removal/generalization*, not by growing a new special case. If a successor sprouts a `special occurrence nonce`, `special crash generation`, `special replication exception`, or `special retry epoch`, that is band-aid divergence → reject and rethink.

Freeze runs these as executable gates, not prose. Each must fire in review, both directions, reviewers inventing new hidden axes:

- **G-owner-node-independent-E:** same complete cause bytes replayed on two nodes with different clocks/profiles/arrival-order/record-availability → byte-identical `intent_projection`, `occurrence_key`, `E`.
- **G-RD0-pin-selection (P0):** the S1={a,b}/S2={a,b,c} partition trace with two selectable durable snapshots for one pre-resolution cause **fails closed** unless the cause/authoritative selector names exactly one. Also asserts the RD0 snapshot/roster artifact **exists** as a content-addressed versioned digest.
- **G-resolver-determinism:** RD0–RD4 fuzz over membership/roster/clock/arrival-order; divergent extent rejected; an undischarged resolver **fails closed / fans out** to literal sub-operations. Negative control: a live-membership-scoped verb whose extent diverges must be rejected.
- **G-OKP-both:** drift-CI rejects a coarser-than-cadence projection (false-merge) AND an over-fine/non-replayable one (false-split), each with a discriminating negative control (R2).
- **G-OKP-immut:** a `registry_version` bump leaves every existing verb's `occurrence_key_projection` byte-identical; a finer OR coarser change is rejected (R3).
- **G-OKP-totality:** totality asserted by failing-to-refute over the enumerated closed field set (L5) using **real/adversarial** fixtures; self-authored fixtures inadmissible.
- **G-carrier-from-cause:** §6 identifies `E1` **solely from the forcing cause's bytes**, with `E1`'s OccurrenceRecord **unreplicated on the firing node** (the replication-lag trace re-run as a negative control). Firing unaffected by record presence/absence.
- **G-carrier-three-state+INVALID:** distinguishes `present / provably-absent / unknown-unreachable / INVALID`; a silent carrier on supersede-intent → `unknown-unreachable` → blocks; `provably-absent` requires an explicit **proof** fixture (§5.1a); a malformed `E_prev` → **INVALID reject**, not a permanent block. Negative control: a silent-carrier cause that a binary read would have fired must now block.
- **G-carrier-conflict-is-reconcile:** a conflicting carrier *record* surfaces as `reconciliation-pending` (#28) and changes neither `E` nor the firing decision.
- **G-carrier-not-in-E:** `E` is invariant under any change to `follows_prior_effect` (present/absent/altered → identical).
- **G-three-homes:** the same-configuration counterexample shows identical ③④ config-lineage digests but distinct carrier values (no home derivable from another; no SoT-creep).
- **G-no-fire-without-cause-bytes (P0):** attempts to fire from only `E`/`work_ref`/bare `cause_ref`/OccurrenceRecord-shadow **fail closed**.
- **G-minter-matrix:** client/schedule/plan each tested with real/adversarial fixtures for occurrence-key stability, predecessor declaration, **absence proof**, and RD0 pin-selection where intensional. Negative control: each minter **partitioned from predecessor state** must emit `present`/`unknown-unreachable`, **never** `provably-absent`.
- **G-extent-digest-audit-only:** mutating/removing `resolved_extent_digest` changes neither `E` nor firing; mutating the cause-pinned extent changes `E`.
- **G-H_E-conformance:** operation layer and ③④ actuator derive identical `E` over a shared corpus **including canonicalization edge cases** (NFC/float/default-materialization), each with a negative control; guards a **single shared `H_E` implementation** (③④ consumes `E` as a value).
- **G-no-occ-standing:** no stored **derived single-word occurrence status** — checked by **semantics, not field-name** (a renamed cache must fail).
- **G-#62-audit:** special-case / enum-growth / priority / magic-number / semantic-axis / failure-prevention / SoT-creep / replaceable-mechanics all run as an explicit checklist.
- **G-limits-declared:** L1 surfaced as discharged (incl. RD0, fail-closed default); L2–L6 surfaced as accepted bounded costs.

Registry discipline (a fail-closed precondition to any of the above): every irreversible verb declares `occurrence_key_projection` + native-key granularity + `resolver_kind` + closed minter-derivation functions; missing → fail-closed.

## Convergence note

v0.1–v0.4 added fields/states/mechanisms per counterexample (divergence). v0.5 achieved node-independence; each successor round then **narrowed** to local safety/liveness refinements and **removed** hidden degrees of freedom. This version adds no occurrence-nonce, crash-generation, replication-exception, or retry-epoch. Under the owner's freeze-gate reject-question — *could the same causal occurrence acquire different identities solely because it was appended, replicated, compacted, rebuilt, or observed on a different node?* — the answer is **No** once RD0 closes pin-selection and §5.1a makes `provably-absent` a proof: the two remaining "yes" paths are shut. The retained history is the evidence this freeze was earned by attack, not by agreement.
