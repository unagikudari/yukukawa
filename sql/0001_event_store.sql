-- Kawa Phase 0 — durable Event log (the Domain SoT).
--
-- Honors the FROZEN contracts:
--   * emit-enforcement: a single durable write path; per-origin (origin_node, origin_seq)
--     gap-free monotone sequence; HLC ordering hint; per-origin hash chain (prev_hash/self_hash).
--   * subject-identity-and-lineage: subject_ref is an immutable UUIDv7.
--   * consistency-and-authority (③④): policy_digest is content-addressed; authority is NOT
--     stored here — this is evidence, not a second authority store.
--
-- Subset of postgresql-physical-schema-v0.3 sufficient for the #53 dogfood loop. Payloads are
-- TYPED per-kind tables, never an authoritative JSONB bag (#57 §6).

CREATE TABLE IF NOT EXISTS events (
    event_id        text        PRIMARY KEY,            -- = self_hash (content-addressed identity)
    origin_node     text        NOT NULL,
    origin_seq      bigint      NOT NULL,               -- per-origin, gap-free, monotone
    hlc             text        NOT NULL,               -- hybrid logical clock: "<phys_ms>.<logical>.<node>"
    recorded_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    kind            text        NOT NULL,
    subject_ref     uuid,                               -- the subject this event is about (UUIDv7)
    actor_ref       text        NOT NULL,               -- accountable emitter
    policy_digest   text,                               -- content-addressed policy in force (③④ §6)
    payload_digest  text        NOT NULL,               -- digest of the typed payload
    prev_hash       text,                               -- previous event's self_hash for this origin
    self_hash       text        NOT NULL,               -- this event's chain link (= event_id)

    UNIQUE (origin_node, origin_seq),
    CHECK (kind IN (
        'plan.created', 'plan.lifecycle_changed',
        'work.derived', 'work.dependency_declared',
        'result.recorded'
    ))
);
CREATE INDEX IF NOT EXISTS events_subject_idx ON events (subject_ref);
CREATE INDEX IF NOT EXISTS events_kind_idx    ON events (kind);
CREATE INDEX IF NOT EXISTS events_origin_idx  ON events (origin_node, origin_seq);

-- Semantic links between events (causal / evidence / supersession), typed edge.
CREATE TABLE IF NOT EXISTS event_links (
    src_event_id text NOT NULL REFERENCES events(event_id),
    dst_event_id text NOT NULL REFERENCES events(event_id),
    link_type    text NOT NULL,
    PRIMARY KEY (src_event_id, dst_event_id, link_type),
    CHECK (link_type IN ('caused_by', 'evidence_for', 'supersedes', 'satisfies'))
);

-- ---- Typed per-kind payloads (no JSONB bag) ----

CREATE TABLE IF NOT EXISTS event_plan (
    event_id    text PRIMARY KEY REFERENCES events(event_id),
    plan_ref    text NOT NULL,
    project_ref text NOT NULL,
    objective   text,
    rationale   text,
    lifecycle   text,                                   -- lifecycle transition carried by this event
    end_reason  text,                                   -- review fix c-1 (physical-schema §8)
    CHECK (lifecycle IS NULL OR lifecycle IN ('draft','reviewing','ready','running','blocked','ended')),
    CHECK (end_reason IS NULL OR end_reason IN ('completed','cancelled','failed','superseded'))
);

CREATE TABLE IF NOT EXISTS event_work (
    event_id         text PRIMARY KEY REFERENCES events(event_id),
    work_ref         text NOT NULL,
    plan_ref         text NOT NULL,
    work_kind        text NOT NULL,                     -- review fix c-2 (implement|review|verify|research|plan)
    role_requirement text,                              -- Implementer|Reviewer|Researcher|Verifier|Planner
    subject_ref      uuid                               -- review fix c-2 (what entity the work is about)
);

CREATE TABLE IF NOT EXISTS event_work_dependency (
    event_id            text PRIMARY KEY REFERENCES events(event_id),
    work_ref            text NOT NULL,
    dependency_work_ref text NOT NULL,
    satisfaction_policy text NOT NULL,
    CHECK (satisfaction_policy IN ('ALL','ANY'))        -- start small (#53 §5)
);

CREATE TABLE IF NOT EXISTS event_result (
    event_id   text PRIMARY KEY REFERENCES events(event_id),
    work_ref   text NOT NULL,
    outcome    text NOT NULL,
    result_ref text NOT NULL,
    summary    text,
    -- execution_unknown is first-class (#53 §10, ③④ F5: authorization-uniqueness != exactly-once)
    CHECK (outcome IN ('success','failure','conflicted','execution_unknown'))
);
