# Kawa Operator Console — Screen → Read-Model Contract (semantic level)

> Handoff for issue #63. Companion to `kawa-console-design-brief.md` and
> `kawa-console-north-star.svg`.
>
> This map binds each screen to a **projection family** and to the **dimensions**
> it reads (from the brief's §1 five-dimension model and §2 standing axes). It is
> deliberately at the **semantic level**.
>
> **CRITICAL — column bindings are placeholders.** The read-model (#51) and the
> operation/effect-identity keystone are still settling, so concrete column names
> are written as explicit TBD placeholders bound to a *dimension*, e.g.
> `current_work.<execution-dimension cols: TBD pending keystone/#51>`.
> **Do not invent final column names.** When #51 / the keystone land, replace each
> `<… : TBD …>` in place; the *dimension binding* on its left is the stable part.

## Legend

- **Projection family** = the materialised read-model a screen reads from
  (names below are provisional handles, themselves TBD → #51).
- **Dimensions** use the brief's vocabulary: `AUTHORITY`, `EXECUTION`,
  `EPISTEMIC`, `HEALTH/REACH`, `PROJECTION-FRESHNESS`, plus the standing axes
  `authority_standing / effect_standing / consumption_standing /
  epistemic_standing` and relations `occurrence / execution / scope / outcome`.
- Every screen additionally reads `PROJECTION-FRESHNESS` (the freshness/completeness
  of *its own* read-model) — this is universal and always surfaced (perf posture).

---

## 1. Situation (overview)

- **Purpose:** the landing sweep — state of all five dimensions at a glance; jump-off.
- **Projection family:** `situation_rollup` (per-dimension summaries; a *view of*
  the other projections, holding no truth of its own).
- **Dimensions read:** **all five** — `AUTHORITY`, `EXECUTION`, `EPISTEMIC`,
  `HEALTH/REACH`, `PROJECTION-FRESHNESS`, each in its own card, **never merged**.
- **Column bindings (RESOLVED — phase 1):** one `situation_rollup` row per card:
  `(dimension, state, gap, count_total, count_nongreen, residue, source_projection, as_of)`.
  - Authority card ← `dimension='AUTHORITY'` — **NO-SOURCE → INCOMPLETE-by-design**, `gap='verifier read-model absent (phase 2+)'`; never derived from raw `security_*` tables
  - Execution card ← rolled up from `current_work.execution/eligibility/coordination` + `work_dispatch.dispatch_state`
  - Epistemic card ← rolled up from `current_claim_standing.standing`
  - Health card ← rolled up from `fleet_node` (§4)
  - Freshness card ← `projection_state.state/error_code/last_recorded_at` — table currently EMPTY ⇒ `INCOMPLETE` with `gap='no projection reports state yet'` until step-2 reducers register rows (a known named gap, not absence-of-evidence; `gap` is CHECK-pinned to INCOMPLETE)
- **MUST:** no combined "overall health" field is read or synthesised; the
  non-green residue is always carried alongside any count.

---

## 2. Evidence / Provenance

- **Purpose:** for a claim/effect, show the evidence chain that grounds it.
- **Projection family:** `evidence_provenance` (event-derived provenance records).
- **Dimensions read:** `EPISTEMIC` (primary), `AUTHORITY` (the standing the
  evidence supports), `PROJECTION-FRESHNESS`.
- **Column bindings (RESOLVED — phase 1):** the screen reads
  `evidence_provenance` ONLY (1:1 projection of `link.asserted`; never raw events):
  - Claim/effect subject ← `evidence_provenance.source_ref` / `target_ref`
  - Evidence items ← rows joined on either ref, with `relation` (11 typed values) + `recorded_at` + `actor_ref`
  - Provenance kind ← `evidence_provenance.provenance` — CHECK-pinned to `event_derived` in phase 1; `inferred` is inadmissible until the Graph phase adds it WITH a named-rule column. An empty result set renders **missing provenance**, never inferred continuity
  - Standing supported ← deferred with the verifier read-model (renders nothing, not a placeholder)
- **MUST:** event-derived vs inferred evidence is visually distinct; a growth/skill
  record, if present, is labelled evidence and is never rendered as authority.

---

## 3. Authority / Proof

- **Purpose:** for a focused operation, render the verifier result and the
  separate standing axes + relations.
- **Projection family:** `authority_proof` (verifier output + grant/standing chain).
- **Dimensions read:** `AUTHORITY` (primary), the four standing axes each
  **separately**, the four relations, and `PROJECTION-FRESHNESS`.
- **Column bindings (TBD):**
  - Verdict ← `authority_proof.<verdict: VALID | INVALID | INCOMPLETE — three-state, TBD pending agent/consistency §5.1 + #51>`
  - Missing-inputs (for INCOMPLETE) ← `authority_proof.<missing-inputs list + satisfied/required count — TBD #51>`
  - `authority_standing` ← `authority_proof.<authority_standing state — TBD keystone>`
  - `effect_standing` ← `authority_proof.<effect_standing state — TBD keystone>`
  - `consumption_standing` ← `authority_proof.<consumption_standing state — TBD keystone>`
  - `epistemic_standing` ← `authority_proof.<epistemic_standing state — TBD keystone>`
  - relations ← `authority_proof.<occurrence | execution | scope | outcome badges — TBD keystone>`
  - grant chain ← `authority_proof.<grant-chain / hop-count — TBD #51>`
- **MUST:** `INCOMPLETE` is first-class (own treatment, non-spinner, shows why +
  count); the four standing axes are shown simultaneously and never reduced to one
  verdict; **no growth/skill column is read here** (§5 of brief).

---

## 4. Fleet

- **Purpose:** per-node, per-dimension state — a node is never one red/green light.
- **Projection family:** `fleet_node` (per-node projected state; STALE when the
  node's own reachability is down).
- **Dimensions read (each its own column/chip — never merged):**
  reachability, workload-health, replication-currency, attestation-strength,
  trust-clarity, Cell-eligibility, quorum-availability. (These are the concrete
  facets under `HEALTH/REACH` + adjacent standing; the invariant is that
  `online ≠ workload healthy ≠ replication current ≠ attestation strong ≠
  trust clear ≠ Cell eligible ≠ quorum available`.) Plus `PROJECTION-FRESHNESS`.
- **Column bindings (RESOLVED — phase 1; ATT/TRU/CEL/QRM deferred with their read-models):**
  - node identity ← `fleet_node.node_ref` (sourced from `DISTINCT events.origin_node`)
  - RCH ← `fleet_node.reachability` — **NO-SOURCE today → UNKNOWN**; `reachability_source` is NULL iff UNKNOWN (CHECK), so a future real source must be named or the write fails
  - WKL ← `fleet_node.workload` — **NO-SOURCE for per-node attribution today → UNKNOWN** (occupancy/dispatch carry agent strings, not a typed node ref; prefix-parsing would invent the mapping)
  - REP ← `fleet_node.replication` + `replication_lag` — no replica registered ⇒ UNKNOWN, never CURRENT
  - ATT / TRU / CEL / QRM ← **deferred** (Authority/Proof & Cell read-models absent; no column exists, so no cell can be invented — #181 rev 2)
  - per-cell freshness ← `fleet_node.reachability_as_of / workload_as_of / replication_as_of`; 'STALE' is render-derived from reachability=CRIT and is unstorable (CHECK)
- **MUST:** when reachability is CRIT the other cells render STALE (freshness),
  not red/green; attestation that cannot be evaluated renders INCOMPLETE, not warn.

---

## 5. Graph / Decision-Lineage

- **Purpose:** how decisions/effects relate — plan → approval → effect →
  observation, as a projection.
- **Projection family:** `decision_lineage` / `knowledge_graph` (**projection,
  not truth**).
- **Dimensions read:** `EPISTEMIC` (primary), `AUTHORITY` (lineage of standing),
  `EXECUTION` (effects), `PROJECTION-FRESHNESS`.
- **Column bindings (TBD):**
  - nodes ← `decision_lineage.<node-ref / kind — TBD pending #51>`
  - edges ← `decision_lineage.<edge-ref — TBD #51>`
  - edge provenance ← `decision_lineage.<edge-provenance: event-derived | inferred — TBD #51>`
    (**drives solid vs dashed — non-negotiable**)
  - as-of / freshness ← `decision_lineage.<graph-as-of — TBD #51>`
- **MUST:** inferred edges are dashed + projection-accent (violet), event-derived
  edges solid; the surface is framed as a projection, never presented as ground
  truth.

---

## 6. MCP / Skill

- **Purpose:** MCP/skill growth per agent — as **evidence**, never authority.
- **Projection family:** `skill_growth` (demonstrated-capability records).
- **Dimensions read:** `EPISTEMIC` (evidence about agents), `PROJECTION-FRESHNESS`.
  **Explicitly NOT `AUTHORITY`.**
- **Column bindings (TBD):**
  - agent ← `skill_growth.<agent-ref — TBD pending #51>`
  - growth metric ← `skill_growth.<skills-delta / window — TBD #51>`
  - (any capability list) ← `skill_growth.<demonstrated-capabilities — TBD #51>`
- **MUST:** rendered in the **neutral track colour** (never severity, never
  accent); carries an explicit `evidence · not authority` marker; **no join from
  a growth column into an authority decision** anywhere in the console.

---

## Cross-screen invariants (bind once, hold everywhere)

1. **Five dimensions never merged** — no screen reads or synthesises a single
   combined status/health field.
2. **Standing axes verbatim** — `authority_standing / effect_standing /
   consumption_standing / epistemic_standing`; renamed only if the keystone
   renames them, then everywhere.
3. **Three-state verdict** — `VALID / INVALID / INCOMPLETE` wherever a verdict is
   read; INCOMPLETE ≠ stale ≠ loading.
4. **Event-derived vs inferred** — any screen reading an edge/provenance
   (Graph, Evidence) renders the two provenances distinctly.
5. **Growth ≠ authority** — `skill_growth` is never an input to `authority_proof`.
6. **Reachability ≠ freshness** — an unreachable node yields STALE projected
   cells, not failed ones.
7. **Freshness always surfaced** — every screen reads and shows the
   freshness/completeness of its own projection (perf posture, #50).

## For the read-model owner (#51) / keystone

Every `<… : TBD …>` above is a hook. The **left side of each binding is stable**
(the dimension / axis / relation); only the bracketed column name is unsettled.
When #51 and the operation/effect-identity keystone land, resolve each placeholder
to its real column **without collapsing two dimensions into one column** — the
separation in §1/§2 is the contract, not the column names.
