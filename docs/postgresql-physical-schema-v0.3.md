# Kawa PostgreSQL Physical Schema v0.3

Status: Draft, current normative candidate
Supersedes: `postgresql-physical-schema-v0.2.md`

## 1. Design rule

The schema should explain Kawa without requiring a protocol manual.

> **Explicit semantics. Minimal mechanics. One obvious path.**

A capable model reading the table and column names should be able to infer what happened, what the Event is about, who acted or observed, what evidence it depends on, and what current state can be reconstructed.

> **Enforcement note.** This document defines the *shape* of the durable tables. The *invariants* over them — append-only immutability, server-attested identity columns, collision-free ids, per-subject ordering, and the single serialized write path — are enforced by `emit-enforcement-contract-v0.1.md`, which is normative for how any row becomes durable. Column definitions here that read as obligations (a `NOT NULL` identity column, a unique id) are made true there, not by convention.

## 2. Durable shape

```text
Durable Domain SoT
├─ events
├─ event_links
├─ event_project
├─ event_problem
├─ event_plan
├─ event_observation
├─ event_claim
├─ event_review
├─ event_finding
├─ event_approval
└─ event_result
```

There are no authoritative `projects`, `problems`, `plans`, `claims`, `facts`, or `work_items` current-state tables.

## 3. Common Event envelope

```sql
-- Phase-0 realized envelope. This matches sql/0001_event_store.sql (the truth on `main`);
-- §3.1 lists the designed-but-not-yet-realized fields so subset vs full is explicit.
CREATE TABLE events (
    event_id        text        PRIMARY KEY,   -- = self_hash: content-addressed identity, NOT an opaque id
    origin_node     text        NOT NULL,       -- authored-by node; immutable across replication (#25)
    origin_seq      bigint      NOT NULL,       -- per-origin, gap-free, monotone (#25)
    hlc             text        NOT NULL,       -- hybrid logical clock; the causal order key (#25 §64), not wall-clock
    recorded_at     timestamptz NOT NULL DEFAULT clock_timestamp(),   -- local receive stamp, never Domain order
    kind            text        NOT NULL,       -- the event type (logical `event_type`); typed per-kind payload tables (#57 §6)
    subject_ref     uuid,                        -- the subject this Event is about (UUIDv7, #28)
    actor_ref       text        NOT NULL,        -- accountable emitter (attested-from-session, not caller-declared)
    policy_digest   text,                        -- content-addressed policy in force (③④)
    payload_digest  text        NOT NULL,        -- digest of the typed payload
    prev_hash       text,                        -- previous event's self_hash for this origin  (= #25 origin_prev_hash)
    self_hash       text        NOT NULL,        -- content hash of this event (= #25 content_hash); event_id = self_hash

    UNIQUE (origin_node, origin_seq)
);
```

> **Origin identity is independent of storage placement (#25).** `(origin_node, origin_seq)` is the **immutable, node-independent** origin coordinate and replication cursor key: an Event authored on node A keeps `origin_node = A, origin_seq = n` on every node it replicates to, and across dump/restore, reindex, archive, and compaction. It is **not** a local append offset, `BIGSERIAL`, or receiving-node position — no storage-placement value may become Event identity, order, or continuity. The per-origin continuity spine (`origin_prev_hash` / `content_hash` / `hlc` / `scope_ref`) is defined normatively by `event-log-and-replication-v0.1.md` and realized in the Phase-0 `events` table (`prev_hash` / `self_hash` / `hlc`). The replication **frontier** is therefore **per-origin** — conceptually `{ origin_node -> highest contiguous origin_seq held }` — and cannot be faithfully represented by a single scalar cursor once multiple origins exist; a scalar Phase-0 cursor is a single-origin simplification, not the architecture.

### 3.1 Phase-0 subset vs full schema (explicit)

The envelope above is the **Phase-0 realized** set (`sql/0001_event_store.sql`). The following are **designed but NOT in the Phase-0 subset** and MUST NOT be presented as implemented:

```text
event_type (distinct column) — Phase-0 realizes the logical event type as `kind`
occurred_at                  — Phase-0 orders by `hlc` (#25 §64), not a wall-clock column
observer_ref / workload_ref (project_ref is realized in the typed event_plan payload table, not the envelope)
correlation_id / causation_id / source_message_id — Phase-0 expresses correlation via the typed
                               event_links table (§5), not envelope columns
schema_version
scope_ref                    — #25 authorized-interest replication filter (Phase 4)
```

Naming map to the #25 canonical continuity spine: `prev_hash` = `origin_prev_hash`, `self_hash` = `content_hash`. **Deliberate Phase-0 deviation from #25:** Phase-0 realizes `event_id = self_hash` (a content-addressed PK). In `event-log-and-replication-v0.1.md` (#25) `event_id` is instead a **time-sortable UUIDv7/ULID** and `content_hash` is a *separate* cross-check field — so #25's time-sortable-`event_id` property is not held by the Phase-0 subset. Unifying the two (a UUIDv7 `event_id` plus a distinct `content_hash`) is deferred; until then this is an explicit subset deviation, not a silent contradiction. Where the consolidated `specification-v0.4.md` uses the logical name `event_type`, the physical column is `kind`; they denote the same concept.

`subject_ref` means exactly the thing the Event is about.

Trusted identity fields are attached by authenticated infrastructure, not caller-declared provenance.

## 4. Actor and observer are distinct

```text
actor_ref     = who intentionally asserted/acted
observer_ref  = who directly observed/measured
subject_ref   = what the Event is about
```

Examples:

```text
plan.revised
  actor_ref   = authoring workload
  subject_ref = Plan

claim.recorded
  actor_ref   = claimant workload/human
  subject_ref = thing claimed about

observation.recorded
  observer_ref = collector workload
  subject_ref  = observed Node / Resource / Service
```

NULL means the role does not apply. If the role applies but identity is unknown, use an explicit unknown principal reference according to schema/policy rather than overloading NULL.

## 5. Semantic links

```sql
CREATE TABLE event_links (
    source_event_id     text NOT NULL REFERENCES events(event_id),
    relation            text NOT NULL,
    target_ref          text NOT NULL,

    PRIMARY KEY (source_event_id, relation, target_ref)
);
```

Initial relation vocabulary:

```text
supports
addresses
reviews
resolves
corrects
supersedes
result_of
based_on
revokes
```

Avoid semantically weak relations such as `related_to`, `associated_with`, `has_ref`, `misc`, or `other`.

## 6. Project Events

```sql
CREATE TABLE event_project (
    event_id            text PRIMARY KEY REFERENCES events(event_id),
    name                text,
    purpose             text,
    end_reason          text
);
```

```text
project.created
project.updated
project.ended
```

Project identity is `events.subject_ref`.

## 7. Problem Events

```sql
CREATE TABLE event_problem (
    event_id            text PRIMARY KEY REFERENCES events(event_id),
    statement           text,
    rationale           text,
    resolution          text
);
```

```text
problem.raised
problem.reframed
problem.resolved
```

Evidence is represented through semantic links rather than copied arrays.

## 8. Plan Events

```sql
CREATE TABLE event_plan (
    event_id            text PRIMARY KEY REFERENCES events(event_id),
    objective           text,
    rationale           text,
    root_cause          text,
    end_reason          text
);
```

```text
plan.proposed
plan.revised
plan.started
plan.ended
```

The Plan-to-Problem relationship uses `addresses` links.

Revision numbers, optimistic concurrency tokens, stale-write basis, and approval hashes are internal mechanics.

## 9. Observation Events

Observation describes a measured/received value. The Event itself is the durable Observation record.

```sql
CREATE TABLE event_observation (
    event_id              text PRIMARY KEY REFERENCES events(event_id),
    predicate             text NOT NULL,
    value_type            text NOT NULL,

    value_text            text,
    value_int             bigint,
    value_numeric         numeric,
    value_bool            boolean,
    value_timestamp       timestamptz,

    unit                  text,
    observation_method    text NOT NULL,
    confidence            real,

    CHECK (
        num_nonnulls(
            value_text,
            value_int,
            value_numeric,
            value_bool,
            value_timestamp
        ) = 1
    )
);
```

Meanings:

```text
subject_ref          = what was observed
observer_ref         = authenticated workload performing observation
observation_method   = trusted deterministic method/tool producing value
predicate            = property observed
value_*              = tool output after deterministic normalization
occurred_at          = time of observation
```

`observation_method` is trusted provenance established by execution, not a caller description.

An LLM inference MUST NOT be stored here merely because it concerns the same predicate/value shape. Inference belongs in `event_claim`.

## 10. Claim Events

Claim records what an accountable Human/Workload asserts or infers about a subject.

The Event itself is the durable Claim record. No independent `claim_ref` is required by default.

```sql
CREATE TABLE event_claim (
    event_id            text PRIMARY KEY REFERENCES events(event_id),
    predicate           text NOT NULL,
    value_type          text NOT NULL,

    value_text          text,
    value_int           bigint,
    value_numeric       numeric,
    value_bool          boolean,
    value_timestamp     timestamptz,

    value_unknown       boolean NOT NULL DEFAULT false,
    rationale           text,

    CHECK (
        (
            value_unknown = true
            AND num_nonnulls(
                value_text,
                value_int,
                value_numeric,
                value_bool,
                value_timestamp
            ) = 0
        )
        OR
        (
            value_unknown = false
            AND num_nonnulls(
                value_text,
                value_int,
                value_numeric,
                value_bool,
                value_timestamp
            ) = 1
        )
    )
);
```

For `claim.recorded`:

```text
subject_ref = thing being claimed about
actor_ref   = authenticated claimant
predicate   = asserted property
value_*     = asserted value
rationale   = optional explanation
```

Evidence and Claim evolution use links:

```text
based_on   -> evidence
supports   -> evidence
corrects   -> prior Claim Event
supersedes -> prior Claim Event
```

`value_unknown=true` explicitly represents an assertion that the value is currently unknown. It is not SQL NULL ambiguity.

Claim authority is not stored as a caller-provided field. Reducer/policy derives applicability from authenticated actor identity, scope, evidence, and policy.

## 11. Review Events

```sql
CREATE TABLE event_review (
    event_id            text PRIMARY KEY REFERENCES events(event_id),
    review_kind         text,
    verdict             text
);
```

```text
review.started
review.completed
```

Review identity is `events.subject_ref`; `reviews` links identify the Plan.

## 12. Finding Events

```sql
CREATE TABLE event_finding (
    event_id            text PRIMARY KEY REFERENCES events(event_id),
    severity            text,
    finding_type        text,
    statement           text,
    resolution          text
);
```

```text
finding.raised
finding.resolved
```

The Finding identity is `events.subject_ref`. The producing Review is linked with `based_on`.

## 13. Approval Events

```sql
CREATE TABLE event_approval (
    event_id            text PRIMARY KEY REFERENCES events(event_id),
    expires_at          timestamptz,
    revoke_reason       text
);
```

```text
approval.granted
approval.revoked
```

For `approval.granted`:

```text
subject_ref = Plan being approved
actor_ref   = Human principal granting approval
```

`approval.revoked` links with `revokes` to the grant Event when the exact grant matters.

Cryptographic bindings remain in the Security plane according to `approval-binding-v0.1.md`.

## 14. Result Events

```sql
CREATE TABLE event_result (
    event_id            text PRIMARY KEY REFERENCES events(event_id),
    outcome             text NOT NULL,
    summary             text,
    started_at          timestamptz,
    finished_at         timestamptz
);
```

```text
result.recorded
```

Use `result_of` links to the Plan/execution/prior Event. Large output is referenced externally.

## 15. Identity criterion

```text
Project  -> enduring entity
Problem  -> enduring entity
Plan     -> enduring entity
Review   -> enduring entity
Finding  -> enduring entity

Observation -> Event sufficient
Claim       -> Event sufficient
Approval    -> grant Event sufficient by default
Result      -> Event sufficient by default
```

Test:

> **Does this concept need to live beyond one Event as an independently evolving thing?**

If no, do not invent another ID.

## 16. NULL rule

A nullable column must have one documented meaning.

Unknown, withheld, conflicted, and not-applicable are different semantic states and MUST NOT be silently collapsed when the distinction matters.

`event_claim.value_unknown` exists specifically to avoid treating “unknown” as “no column value happened to be present.”

## 17. Typed payload rule

There is no Domain `jsonb`, `metadata`, `extra`, or generic attributes column.

A new durable concept must be represented by:

```text
explicit typed column
explicit semantic link
explicit external artifact/resource reference
new typed schema version
```

## 18. Indexes

Start with obvious hot-path indexes:

```sql
CREATE INDEX events_project_recorded_idx
    ON events (project_ref, recorded_at DESC)
    WHERE project_ref IS NOT NULL;

CREATE INDEX events_subject_recorded_idx
    ON events (subject_ref, recorded_at DESC);

CREATE INDEX events_type_recorded_idx
    ON events (event_type, recorded_at DESC);

CREATE INDEX events_causation_idx
    ON events (causation_id)
    WHERE causation_id IS NOT NULL;

CREATE INDEX event_links_target_relation_idx
    ON event_links (target_ref, relation);

CREATE INDEX event_observation_predicate_idx
    ON event_observation (predicate);

CREATE INDEX event_claim_predicate_idx
    ON event_claim (predicate);
```

Add further indexes only from measured hot paths.

## 19. 40 ms SLO

```text
single Event emit                 p95 < 40 ms
current Project/Problem/Plan get p95 < 40 ms
work.next                         p95 < 40 ms
small current projection query   p95 < 40 ms
```

Historical semantic search, rebuild, federation reconciliation, and archive recovery are outside this local hot-path SLO.

## 20. Projections remain disposable

```text
current_projects
current_problems
current_plans
current_reviews
current_findings
current_facts
current_work
```

must satisfy:

```text
DROP projection
→ replay Events
→ reconstruct equivalent current state
```

Claim/Observation history remains in Events/payloads; a current Claim view, if materialized, is also disposable.

## 21. Naming test

Before accepting a table, column, relation, or Event type, ask:

```text
Would a capable LLM infer its purpose from the name?
Is there only one plausible interpretation?
Does another field already express the same fact?
Can a generic word be replaced by a semantic word?
Can a mechanical concept be removed from the public schema?
```

## 22. Core shape

```text
Observation = measured evidence
Claim       = accountable assertion
Fact        = derived current interpretation

Events
  ↓ deterministic reducers
Current Understanding
  ↓
Work
```

> **Observe what happened. Claim what you infer. Derive what is currently accepted as Fact.**
