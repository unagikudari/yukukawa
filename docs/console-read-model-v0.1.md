# Kawa Console Read-Model v0.1

Status: **Draft — NOT frozen.** The concrete PostgreSQL read-model for the Console, Fleet, Graph/Decision-Lineage, MCP, and Skill-Growth surfaces. Freezes only when its acceptance gate (§13) is met.

Consolidates: #51 (this read-model schema), #46 (Operator Surface), #47 (MCP/Skill growth), #48 (Fleet), #49 (Graph + Decision Lineage), #50 (performance contract), #53 (Work-driven Agent Runtime).

**Consumes — never duplicates:**
- `postgresql-physical-schema-v0.3.md` — the durable **Event tables** (`events`, `event_*`) are the Domain SoT this read-model is rebuilt from. This slab does **not** re-declare them.
- `reducer-projection-contract-v0.2.md` — the projection **semantics** (per-entity reducers, rebuildability §21, versioning §22, no-hidden-LLM-authority §23). This slab gives those projections a **concrete indexed physical shape** for the Console; it does not redefine their meaning.
- `consistency-and-authority-v0.1.md` (**FROZEN normative v0.1**) — the authority standing, three-state verifier (`VALID/INVALID/INCOMPLETE`), configuration lineage, and the revisable-vs-consume-once invariant classes. Authority **columns here are projections of Receipts**, never a second authority store.
- `event-log-and-replication` (frontier/cursor), `subject-identity-and-lineage` (`subject_ref`), `approval-binding` #32 (`approval_state`).

> **Events remain the Domain SoT. Projection tables are disposable acceleration structures.**
> **Pay the cost when Events change, not every time a human asks what is true now.**
> **The Event log explains history. The projection schema explains now.**

---

## 1. Position: this is the read side, and only the read side

The existing layer already has (a) the durable Event tables (physical-schema §3–§17) and (b) the projection *semantics* (reducer contract). What was missing — and what #51 asks for — is the **concrete, indexed Console read-model**: the physical DDL for the `current_*` projections that physical-schema §20 only *names*, plus the new projections the Console/Fleet/Graph/MCP/Skill/Runtime surfaces (#46–#50, #53) require.

Nothing in this slab is authoritative. Every table below satisfies the projection contract (§2) and is rebuildable from Events/Receipts/telemetry. If any projection column ever disagrees with a Receipt or an Event, **the Event/Receipt wins and the projection is wrong and must be rebuilt** — this is stated once here and assumed everywhere below.

## 2. Projection contract (binding)

Every table in this slab MUST satisfy (restating reducer-contract §21/§25 in physical terms):

```text
authoritative = false
rebuildable   = true
writers       = reducer / projection worker only
readers       = Console / MCP / local API
```

**The `writers`/`readers` split is enforced at the database, not just by convention (PR#55 review a-2):** the Console / MCP / query-API role holds `GRANT SELECT` on every projection table and **no** `INSERT/UPDATE/DELETE`; only the reducer/projection-worker role may write them. This makes "a Console backend directly updates `current_work.coordination`" a privilege error, not a code-review catch — the same DB-enforced discipline the Event log uses for append-only.

Required recovery property (the definition of "disposable"):

```text
DROP <every table in this slab>
→ replay retained Events / Receipts / retained telemetry
→ rebuild an equivalent current projection
```

Two data-structure rules, load-bearing for the Ten-Year design:
- **Typed columns over generic `jsonb`/`metadata` bags.** Where a field is part of the read contract, it gets a typed column with a `CHECK`ed domain. A `jsonb` bag is allowed only for genuinely open, non-queried annotation, never for a field the Console filters or a state the system reasons about.
- **No giant state enum.** Multi-dimensional state (execution, plan lifecycle) is expressed as **separate typed columns**, one per orthogonal dimension (§5), not one collapsed enum and not booleans that can contradict.

Performance (from #50, restated as a target not a Core claim): a normal local current-state read is an indexed lookup, `p95 ≤ 40 ms`; `> 1 s` for a local current-state question is first a schema/projection/query-path failure, never "inherent to Event Sourcing." Latency budgets are §14 Implementation, and **never override the three-state authority honesty of §7**.

## 3. Projection control plane

Freshness and rebuild state are tracked without becoming Domain truth.

```sql
CREATE TABLE projection_state (
    projection_name      text PRIMARY KEY,
    schema_version       integer     NOT NULL,
    last_event_id        text,                     -- cursor into the Domain Event log
    last_recorded_at     timestamptz,
    last_local_sequence  bigint,                   -- local frontier (event-log-and-replication)
    state                text        NOT NULL,
    rebuild_started_at   timestamptz,
    rebuild_completed_at timestamptz,
    error_code           text,
    error_summary        text,
    updated_at           timestamptz NOT NULL DEFAULT clock_timestamp(),

    CHECK (state IN ('current','lagging','rebuilding','failed','unavailable'))
);
```

The Console MAY display `state` and lag (`now() - last_recorded_at`) as an explicit freshness signal, but MUST NOT read `projection_state` as Domain evidence. A `lagging`/`rebuilding`/`failed` projection is surfaced to the operator as such — **projection lag is a first-class state, not a silent stale read** (#46 risk list).

## 4. Current semantic projections (concrete shape for physical-schema §20)

The `current_*` family named in physical-schema §20 gets its Console-facing DDL here. Two load-bearing tables in full; the rest follow the same pattern (a `*_ref` PK, the entity's typed current fields, `latest_event_id`/`latest_recorded_at`, and any authority/approval columns projected from ③④/#32).

### 4.1 current_plans

```sql
CREATE TABLE current_plans (
    plan_ref            text PRIMARY KEY,
    project_ref         text NOT NULL,
    objective           text NOT NULL,
    rationale           text,
    lifecycle           text NOT NULL,            -- Plan lifecycle (§5), NOT collapsed with satisfaction
    end_reason          text,                     -- why an ended Plan ended (PR#55 review c-1; physical-schema §8)
    revalidation        text NOT NULL,            -- is the current derivation still valid?
    approval_state      text NOT NULL,            -- projected from ③④/#32, NOT decided here
    latest_event_id     text NOT NULL,
    latest_recorded_at  timestamptz NOT NULL,
    current_fingerprint text,                     -- digest of the reduced state, for cheap change-detect

    CHECK (lifecycle IN ('draft','reviewing','ready','running','blocked','ended')),
    CHECK (end_reason IS NULL OR end_reason IN ('completed','cancelled','failed','superseded')),
    CHECK (revalidation IN ('clear','required')),
    CHECK (approval_state IN ('not_required','required','pending','granted','stale','revoked','expired'))
);
```

`approval_state` values are the **frozen** ③④/#32 supersession/stale-approval vocabulary — this column is a projection of the approval Receipts, never a place the Console sets approval.

### 4.2 Remaining current_* projections

`current_projects`, `current_problems`, `current_reviews`, `current_findings`, `current_facts` follow 4.1's pattern. Two carry authority-relevant columns worth pinning now:
- `current_facts` carries the derived Fact state `CHECK (fact_state IN ('clear','conflicted','unknown'))` — the epistemic triad (spec §5.3), the read-side twin of the ③④ verifier's `VALID/INVALID/INCOMPLETE`. A `conflicted` Fact is rendered as conflicted, never silently resolved to one side.
- `current_reviews` carries the review verdict vocabulary; conflicting reviews leave the reviewed input `conflicted`, they do not vote a winner (#53 §4).

## 5. Work & execution projections (#53)

Work is a projection — a rebuildable current view of "what is actionable now," never a second authoritative queue (#53 §3).

### 5.1 current_work

```sql
CREATE TABLE current_work (
    work_ref             text PRIMARY KEY,
    plan_ref             text NOT NULL,
    work_kind            text NOT NULL,           -- what the Work is (implement|review|verify|research|plan) — PR#55 review c-2
    subject_ref          uuid,                    -- the entity the Work is about — PR#55 review c-2
    role_requirement     text,                    -- Implementer|Reviewer|Researcher|Verifier|Planner (a requirement, not an owner)
    dependency_total     integer NOT NULL DEFAULT 0,
    dependency_satisfied integer NOT NULL DEFAULT 0,
    dependency_conflicted integer NOT NULL DEFAULT 0,
    -- multidimensional execution state (#53 §12): orthogonal columns, never one enum
    awareness            text NOT NULL,
    eligibility          text NOT NULL,
    coordination         text NOT NULL,
    authority            text NOT NULL,           -- projected from ③④; see §7
    execution            text NOT NULL,
    ready_at             timestamptz,
    latest_event_id      text NOT NULL,
    updated_at           timestamptz NOT NULL DEFAULT clock_timestamp(),

    CHECK (awareness    IN ('unknown','known','current','stale')),
    CHECK (eligibility  IN ('unknown','eligible','ineligible','insufficient_context')),
    CHECK (coordination IN ('unclaimed','claimed_local','claimed_confirmed','contested','expired','not_required')),
    CHECK (authority    IN ('not_required','pending','authorized','revoked','expired','incomplete')),
    CHECK (execution    IN ('idle','ready','executing','retryable','blocked','execution_unknown','result_recorded','finished'))
);
```

The five dimensions stay separable so states like *`current` + `eligible` + `authority=revoked` + `blocked`* remain distinct from *`stale` + `eligibility=unknown` + `authority=incomplete` + `idle`* (#53 §12). `coordination` is a **hint, never authority** (§11 of #53): `claimed_confirmed` is not proof no other side executed.

### 5.2 current_work_dependency

```sql
CREATE TABLE current_work_dependency (
    work_ref            text NOT NULL,
    dependency_work_ref text NOT NULL,
    dependency_kind     text NOT NULL,
    satisfaction_policy text NOT NULL,
    dependency_state    text NOT NULL,
    result_ref          text,                     -- the Result Event that satisfied it, if any
    updated_at          timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (work_ref, dependency_work_ref),

    CHECK (satisfaction_policy IN ('ALL','ANY')),  -- start small (#53 §5); AT_LEAST_N/FIRST_SUCCESS only on real need
    -- a permanently-failed dependency has a TERMINAL state, not 'pending' forever (PR#55 review c-3)
    CHECK (dependency_state IN ('pending','satisfied','conflicted','failed'))
);
```

Readiness is result-driven: *Results satisfy dependencies; dependencies make Work actionable.* A `conflicted` dependency may still satisfy readiness while leaving the downstream input conflicted (#53 §4) — hence `dependency_conflicted` is counted, not hidden.

### 5.3 plan_execution_summary

Purpose-built so a Plan's execution state is a small indexed read, never a history scan (#53 §13, #50). **Three orthogonal dimensions, never one `done` bit:**

```sql
CREATE TABLE plan_execution_summary (
    plan_ref          text PRIMARY KEY,
    lifecycle         text NOT NULL,   -- draft|reviewing|ready|running|blocked|ended
    satisfaction      text NOT NULL,   -- unknown|unsatisfied|partially_satisfied|satisfied|conflicted|unverifiable
    execution_anomaly text NOT NULL,   -- none|duplicate|divergent|unauthorized|uncertain|mixed
    updated_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (lifecycle    IN ('draft','reviewing','ready','running','blocked','ended')),
    CHECK (satisfaction IN ('unknown','unsatisfied','partially_satisfied','satisfied','conflicted','unverifiable')),
    CHECK (execution_anomaly IN ('none','duplicate','divergent','unauthorized','uncertain','mixed'))
);
```

A valid reconciled state is `lifecycle=ended, satisfaction=satisfied, execution_anomaly=duplicate` (#52/#53): success and duplicate can both be true.

## 6. Runtime projections (#53 §8/§9/§16)

Runtimes are replaceable occupants of Work; these projections describe *presence and occupancy*, never ownership. Core reasons about `runtime_instance`, not tmux/systemd (#53 §8) — the mechanism is a Node-local adapter concern (§14).

```sql
CREATE TABLE runtime_instance (
    runtime_ref       text PRIMARY KEY,
    node_ref          text NOT NULL,
    workload_ref      text,
    agent_profile_ref text,
    role_ref          text,
    work_ref          text,                        -- currently occupied Work (may be NULL)
    runtime_state     text NOT NULL,
    started_at        timestamptz,
    last_seen_at      timestamptz,
    CHECK (runtime_state IN ('healthy','busy','idle','suspect','stalled','unreachable','terminated','unknown'))
);

CREATE TABLE runtime_health (
    runtime_ref     text PRIMARY KEY REFERENCES runtime_instance(runtime_ref),
    node_ref        text NOT NULL,
    current_work_ref text,
    process_state   text,
    last_activity_at   timestamptz,
    last_tool_call_at  timestamptz,
    last_kawa_call_at  timestamptz,
    progress_seq    bigint,                         -- monotone progress counter; presence != progress
    updated_at      timestamptz NOT NULL DEFAULT clock_timestamp()
);
```

```sql
-- single-occupancy projection (PR#55 review a-1): work_ref PK removes the multi-occupant
-- ambiguity a partitioned failover could otherwise create, so Work survives runtime loss cleanly.
CREATE TABLE runtime_work_occupancy (
    work_ref    text PRIMARY KEY,
    runtime_ref text NOT NULL REFERENCES runtime_instance(runtime_ref),
    since       timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);
```

`runtime_work_occupancy` records the current occupant so Work survives runtime loss. Rules pinned from #53 §9/§10:
- **Heartbeat proves presence, not progress** (`last_seen_at` vs `progress_seq` are distinct).
- **Absence of heartbeat proves uncertainty, not death** — `suspect`/`stalled` precede `terminated`; a partitioned Node may be locally healthy while remotely `unreachable`.
- High-frequency heartbeat/telemetry is retained **separately from the Domain Event log** and summarized here — it is operational data, not Domain truth.

## 7. Authority / proof read projection (#46 §3)

So an operator sees authority standing without inspecting implementation tables — and so RFC #43 ("does Kawa hide consensus/authority bookkeeping from callers?") is **empirically testable**.

```sql
CREATE TABLE authority_standing (
    authority_key       text PRIMARY KEY,
    invariant_class     text NOT NULL,             -- from FROZEN ③④ §2.1
    current_config_id   text,
    authority_epoch     bigint,
    verifier_state      text NOT NULL,             -- the three-state verifier, projected
    last_receipt_digest text,
    partition_state     text NOT NULL,
    updated_at          timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (invariant_class IN ('revisable','consume_once_fungible','consume_once_singular')),
    CHECK (verifier_state  IN ('valid','invalid','incomplete')),
    CHECK (partition_state IN ('available','blocked_fail_closed','recovering'))
);
```

Load-bearing boundaries:
- `verifier_state = incomplete` is a **first-class rendered state**, not a loading spinner (§2/#46) — "unknown is neither false nor authority" (frozen ③④ §5.1).
- A **`consume_once_singular`** key renders as *consumed / authorized-once*, never a revisable toggle the operator can re-flip (frozen ③④ §3, F7/F8). The projection cannot represent "re-authorize E" as an ordinary action — it can only surface a cancellation/non-execution proof state, matching the contract.
- This table is a projection of Receipts; dropping and rebuilding it loses **zero** authority (the Receipts are the truth).

## 8. Graph / Decision-Lineage projections (#49)

A typed-graph projection backing the Graph Explorer's lenses (Schema/Live/Decision-Lineage/Evidence/Authority/Fleet/Skill/Code/Unified). The load-bearing rule is edge provenance:

```sql
CREATE TABLE graph_edge (
    src_ref    text NOT NULL,
    dst_ref    text NOT NULL,
    edge_type  text NOT NULL,
    provenance text NOT NULL,                      -- how this edge is known — the honesty flag
    evidence_event_id text,                        -- required when provenance='event_derived'
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (src_ref, dst_ref, edge_type),
    CHECK (provenance IN ('event_derived','inferred','schema_static')),
    CHECK (provenance <> 'event_derived' OR evidence_event_id IS NOT NULL)
);
```

**An `inferred` edge is never rendered as an `event_derived` one.** The graph is a projection ("Graph is a projection, not truth," #49); it must never manufacture lineage. `decision_lineage` is a purpose-built indexed projection (Plan → rationale → authority → realizing artifacts), served from indexed rows, never reconstructed by a history scan on each read (#50/#53). `graph_node` mirrors this with a typed `node_type`.

## 9. Fleet projections (#48)

Fleet standing is **multi-dimensional by construction** — a single green/red would collapse security, consistency, and liveness. The distinctions are separate typed columns:

```sql
CREATE TABLE fleet_node (
    node_ref          text PRIMARY KEY,
    lifecycle         text NOT NULL,   -- provisioning|active|degraded|partitioned|rejoining|retiring|retired|distrusted|unknown
    connectivity      text NOT NULL,
    replication       text NOT NULL,   -- converged|behind|partitioned|incomplete|unknown  (event-log frontier, PR #25)
    attestation       text NOT NULL,
    trust_standing    text NOT NULL,   -- from #21 distrust projection
    cell_eligibility  text NOT NULL,   -- derived from FROZEN ③④; NOT operator-settable
    last_seen_at      timestamptz,
    CHECK (lifecycle IN ('provisioning','active','degraded','partitioned','rejoining','retiring','retired','distrusted','unknown')),
    CHECK (replication IN ('converged','behind','partitioned','incomplete','unknown'))
);
```

`cell_eligibility` is **derived** from the frozen authority contract; the Fleet view may *request* a semantic action but an operator must never *set* eligibility — that transition is `Cell.EpochSuccession` (CP quorum), not a UI toggle (#48, frozen §4). "Reachable but missing relevant history" must read `replication != converged`, never fully-current. *Do not infer authority from connectivity alone.*

## 10. MCP / Skill-Growth projections (#47)

Capability-growth metrics (tool-selection accuracy, L1–L5 layers, skill effectiveness over comparable Results) are read projections. **One hard guard, pinned:** a growth/skill score **stays AP evidence and never becomes an authority signal.** A high score may *inform* a human or a Cell; it can change authority standing only through an explicit ③④ `Cell.EpochSuccession`, never by auto-promotion — otherwise the analytics layer silently becomes an authority plane (the anti-pattern frozen §1 rejects). Growth is measured against held-out comparable Results, guarding the recursive-drift risk (a skill certifying its own improvement is unfalsifiable, #47).

## 11. Authorization-before-disclosure (the #39 boundary)

The authorization predicate is carried **into** the indexed read path, not applied by broad-fetch-then-filter:

```text
SELECT ... FROM current_work
 WHERE <authorization predicate>       -- an index condition, evaluated in the lookup
   AND <query predicate>;
```

Fetch-then-filter is both a disclosure leak and the latency failure mode #50 forbids. The authorization model itself is **R6** (deferred; §11 Boundaries of frozen ③④) — this slab *consumes* that boundary, it does not define who may see what. Until R6 lands, each Console read documents the predicate it enforces so R6 can formalize it without a schema rewrite.

## 12. Boundary with the frozen Authority contract

This slab is strictly downstream of `consistency-and-authority-v0.1` (FROZEN). It **cannot** and does not:
- store authority (every authority column is a projection of Receipts, §7);
- let a projection outrank a Receipt (on disagreement the Receipt wins; the projection is rebuilt);
- represent a `consume_once_singular` effect as revisable (§7), or a lease/claim as authority (§5/§6, #53 §11);
- resolve a `conflicted` Fact / review to one side (§4).

Because of this, a Core change to ③④ never *breaks* this slab's semantics — at most it changes which projected values are possible. This slab is therefore a **Profile/Implementation** concern under frozen ③④ §10, not a Core-adjacent one.

## 13. Acceptance gate (before this draft is frozen)

1. Every table satisfies §2 (`DROP → replay → rebuild` produces an equivalent projection) — demonstrated, not asserted.
2. No `jsonb`/`metadata` bag holds a field the Console filters or the system reasons about (§2 data-structure rule), verifiable by the naming/typing test (physical-schema §21).
3. Multidimensional state is separate typed columns; no collapsed enum, no contradictable booleans (§5).
4. Every authority/approval column is provably a projection of a Receipt/Event and is dropped-and-rebuilt with zero authority loss (§7/§12).
5. Graph edges carry `provenance`; no `inferred` edge is presentable as `event_derived` (§8).
6. Fleet standing keeps `online / quorum / eligible / attested / trust` as distinct columns; no single green/red (§9).
7. A growth/skill score cannot alter authority standing except via an explicit ③④ decision (§10).
8. The authorization predicate is expressible as an index condition (§11); a fetch-then-filter path is a conformance failure.
9. The #53 §18 runtime acceptance tests hold against these projections (readiness survives runtime loss; missing wake does not lose readiness; heartbeat loss yields uncertainty before termination; side-effecting Work with `execution=execution_unknown` is verified before unsafe retry).
10. Independent adversarial review (per the project discipline) finds no surviving case where a projection becomes authoritative or a disclosure precedes authorization.

### 13.1 Review fold (round 1)
An independent adversarial lane (vendor-B) returned **REFUTE-FOR-FREEZE** — the design skeleton conforms but with fixable DDL/CHECK gaps. Folded here: **a-1** single-occupancy `runtime_work_occupancy` DDL (§6); **a-2** DB-enforced `GRANT` boundary (§2); **c-1** `current_plans.end_reason` (§4.1); **c-2** `current_work.work_kind`/`subject_ref` (§5.1); **c-3** terminal `dependency_state='failed'` (§5.2). These are mirrored in the Phase 0 schema `sql/0002_projections.sql`, which is the executable check on this doc. *Honest gap:* the review's full text lived only in the broker task note and was truncated past c-3, so a sixth finding could not be recovered verbatim — a concrete motivation for #56 (a Kawa Result is durable and typed, not a truncatable note). A follow-up Work tracks recovering it before this slab's freeze.

## 14. Replaceable mechanics deliberately left open (#53 §17)

Not part of this read-model contract; Node-local adapter / Implementation concerns: wake transport (PostgreSQL `LISTEN/NOTIFY`, Unix socket, local queue, process activation), heartbeat/poll intervals, graph-store engine (none required for correctness; any future graph DB stays disposable), tmux/systemd/container/runtime mechanics, CLI argument conventions. **Notification is a latency optimization; a missed notification MUST NOT lose READY Work, because readiness lives in the projection (§5), not the signal.**
