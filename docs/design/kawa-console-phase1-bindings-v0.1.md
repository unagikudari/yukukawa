# Console Phase-1 Bindings — resolved sources, DDL, invariant predicates

Status: step-1 artifact of plan-console-phase1 (#181 rev 3). Companion to
`kawa-console-screen-map.md` (whose phase-1 `TBD` hooks this resolves in place)
and `sql/0021_console_phase1_projections.sql` (the DDL this document explains).

Everything here is **measured against the live schema and event log
(2026-08-17)**, not assumed. Where no source exists, the binding says so and the
cell renders `UNKNOWN` / `INCOMPLETE` — no invented values, ever (#181 rev 2
epistemic ground rules).

## 1. Measured substrate

Event kinds present in the log: `result.recorded`, `work.derived`,
`observation.recorded`, `work.dependency_declared`, `plan.lifecycle_changed`,
`plan.created`, `link.asserted`, `work.retired`, `claim.recorded` — and
**nothing else**. In particular there is **no reachability / heartbeat /
replication-lag / node-incarnation event kind** in the live log, and
`projection_state` exists but holds **zero rows** (nothing reports projection
freshness yet). `security_attestation` holds zero rows. These absences are
load-bearing for the bindings below.

## 2. Situation screen — per-card bindings

| Card | State source (real columns) | Freshness source | Honest phase-1 floor |
|---|---|---|---|
| EXECUTION | `current_work.execution`, `current_work.eligibility`, `current_work.coordination` + `work_dispatch.dispatch_state` (counts; residue = non-green work_refs) | max(`current_work.updated_at`) | measurable today |
| EPISTEMIC | `current_claim_standing.standing` counts (post-nucleus table — `console-read-model-v0.1.md`'s `current_facts` never landed; Fact retired, see §6) | max(`current_claim_standing.updated_at`) | measurable today |
| PROJECTION_FRESHNESS | `projection_state.state`, `error_code`, `last_recorded_at` per projection | `projection_state.updated_at` | **INCOMPLETE until step-2 reducers register their rows** — the table is empty, and that is a *known, named* gap (`gap="no projection reports state yet"`), not mere absence-of-evidence; the card must never show green. (`UNKNOWN` never carries a gap — gap is CHECK-pinned to `INCOMPLETE`; checkpoint-review fix) |
| HEALTH_REACH | roll-up of `fleet_node` rows (below) | max(`fleet_node.updated_at`) | mostly UNKNOWN (see Fleet) |
| AUTHORITY | **NO-SOURCE → INCOMPLETE-by-design.** `gap="verifier read-model absent (phase 2+)"` — never derived from raw `security_*` tables | `situation_rollup.as_of` | INCOMPLETE with named gap |

The rollup is a *view over* the other projections (`situation_rollup.source_projection`
names which one per row); it holds no truth and no combined field exists.

## 3. Fleet screen — per-cell bindings

| Cell | Binding | Measured reality |
|---|---|---|
| node identity | `DISTINCT events.origin_node` (live: `local`, `panoplia`, `test`) | real |
| reachability (RCH) | **NO-SOURCE → UNKNOWN.** No reachability event kind exists. `fleet_node.reachability_source` is NULL iff UNKNOWN (CHECK-enforced), so the day a real source event lands it must be named or the write fails | UNKNOWN for every node |
| workload (WKL) | `runtime_work_occupancy` (occupied work per runtime) + `work_dispatch.dispatch_state` non-terminal counts | measurable today |
| replication (REP) | per-origin `max(events.origin_seq)` vs the pulling node's view. **No replica is registered on this node → UNKNOWN**, never CURRENT (CHECK: lag travels with LAGGING exactly — positive, else NULL) | UNKNOWN |
| attestation (ATT) | **ABSENT from phase 1** — Authority/Proof concern (#181 rev 2); the DDL has no column, so a screen cannot render what a reducer cannot invent | — |
| per-cell freshness | each cell's own `*_as_of` column (NOT NULL enforced per written state) | real |

STALE is **render-derived only** (reachability = CRIT ⇒ sibling cells render
STALE): the CHECKs exclude 'STALE' from every stored state, so the projection
can never launder a render rule into stored fact. Note the rule fires on
measured `CRIT`, **not** on `UNKNOWN` — an unmeasured node does not stale its
siblings.

## 4. Evidence screen — bindings

Reads **`evidence_provenance` only** (1:1 projection of `link.asserted` events;
Console never touches raw `events`/`event_link`). Live data: 16 links across
`supports`(2) / `supersedes`(1) / `based_on`(12) + 1 recent. `provenance` is
CHECK-pinned to `event_derived`; widening to `inferred` is the Graph-phase
change and must arrive together with a named-rule column. An event/claim with
no rows here renders **"missing provenance"** — absence is shown as absence.

## 5. The seven invariants as literal predicates

Each is a testable proposition over the DDL + fixtures
(`tests/test_console_phase1_contract.py` executes all of them):

1. **No merged dimension** — `situation_rollup.dimension` admits exactly the
   five names; no sixth "overall" value inserts; no aggregate column exists.
2. **Standing axes verbatim** — any standing name appearing in projections is
   one of `authority_standing / effect_standing / consumption_standing /
   epistemic_standing` (phase 1: none appear; the predicate guards additions).
3. **Three-state verdict** — every state enum admits its UNKNOWN/INCOMPLETE
   member; `state='INCOMPLETE'` without a non-empty `gap` is a constraint
   violation, and `gap` on any state other than INCOMPLETE is one too
   (UNKNOWN = absence-of-evidence, INCOMPLETE = known named gap; the schema
   keeps the boundary).
4. **Provenance distinction** — `evidence_provenance.provenance` cannot store
   anything but `event_derived`; missing chains are represented by row absence,
   never by a placeholder edge.
5. **Growth ≠ authority** — no `skill_growth` relation exists
   (`to_regclass('skill_growth') IS NULL`) and no projection carries a
   growth-named column.
6. **Reachability ≠ freshness** — 'STALE' is unstorable in every state CHECK
   (insert attempts fail); reachability CRIT with fresh sibling `*_as_of`
   values coexist in fixtures (staleness derives at render, from CRIT only).
7. **Freshness always surfaced** — `situation_rollup.as_of` NOT NULL;
   `fleet_node` carries per-cell `*_as_of`; a written (non-UNKNOWN) cell state
   without its `as_of` is rejected by the contract tests.
8. *(ground rule, tested with 6)* **Absence ≠ negative** — `UNKNOWN` and
   `CRIT`/`LAGGING` are distinct stored states; fixtures exercise both
   directions and no path collapses one into the other.

## 6. Pre-nucleus vocabulary reconciliation

`console-read-model-v0.1.md` §4.2 references `current_facts` /
`fact_state('clear','conflicted','unknown')`. That table was designed before
the epistemic nucleus (spec v0.5 §2.6) retired Kawa-owned Fact; it was never
created. The live epistemic read-model is `current_claim_standing`
(`standing`, default `'unevaluated'`). The doc now carries a supersession note
pointing here; the screen map's EPISTEMIC bindings use
`current_claim_standing` throughout.
