-- Kawa Phase 0 — disposable read-model projections (console-read-model-v0.1 subset).
--
-- authoritative = false, rebuildable = true. Writers = reducer/projection worker only.
--   DROP <these tables> → replay `events` → rebuild equivalent projections.
-- Authority columns are projections of Receipts, never a second authority store (③④ §12).
-- Review fixes from PR #55 folded here: end_reason (c-1), work_kind/subject_ref (c-2),
-- terminal dependency state (c-3), explicit runtime_work_occupancy (a-1).

CREATE TABLE IF NOT EXISTS projection_state (
    projection_name      text PRIMARY KEY,
    schema_version       integer     NOT NULL,
    last_event_id        text,                          -- cursor into the Domain Event log
    last_recorded_at     timestamptz,
    last_local_sequence  bigint,
    state                text        NOT NULL,
    rebuild_started_at   timestamptz,
    rebuild_completed_at timestamptz,
    error_code           text,
    error_summary        text,
    updated_at           timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (state IN ('current','lagging','rebuilding','failed','unavailable'))
);

CREATE TABLE IF NOT EXISTS current_plans (
    plan_ref           text PRIMARY KEY,
    project_ref        text NOT NULL,
    objective          text NOT NULL,
    rationale          text,
    lifecycle          text NOT NULL,
    end_reason         text,                             -- review fix c-1
    latest_event_id    text NOT NULL,
    latest_recorded_at timestamptz NOT NULL,
    CHECK (lifecycle IN ('draft','reviewing','ready','running','blocked','ended')),
    CHECK (end_reason IS NULL OR end_reason IN ('completed','cancelled','failed','superseded'))
);

CREATE TABLE IF NOT EXISTS current_work (
    work_ref              text PRIMARY KEY,
    plan_ref              text NOT NULL,
    work_kind             text NOT NULL,                 -- review fix c-2
    subject_ref           uuid,                          -- review fix c-2
    role_requirement      text,
    dependency_total      integer NOT NULL DEFAULT 0,
    dependency_satisfied  integer NOT NULL DEFAULT 0,
    dependency_conflicted integer NOT NULL DEFAULT 0,
    -- orthogonal execution dimensions (#53 §12), never one enum:
    awareness    text NOT NULL DEFAULT 'current',
    eligibility  text NOT NULL DEFAULT 'unknown',
    coordination text NOT NULL DEFAULT 'not_required',
    authority    text NOT NULL DEFAULT 'not_required',
    execution    text NOT NULL DEFAULT 'idle',
    ready_at        timestamptz,
    latest_event_id text NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (awareness    IN ('unknown','known','current','stale')),
    CHECK (eligibility  IN ('unknown','eligible','ineligible','insufficient_context')),
    CHECK (coordination IN ('unclaimed','claimed_local','claimed_confirmed','contested','expired','not_required')),
    CHECK (authority    IN ('not_required','pending','authorized','revoked','expired','incomplete')),
    CHECK (execution    IN ('idle','ready','executing','retryable','blocked','execution_unknown','result_recorded','finished'))
);

CREATE TABLE IF NOT EXISTS current_work_dependency (
    work_ref            text NOT NULL,
    dependency_work_ref text NOT NULL,
    satisfaction_policy text NOT NULL,
    dependency_state    text NOT NULL,
    result_ref          text,
    updated_at          timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (work_ref, dependency_work_ref),
    CHECK (satisfaction_policy IN ('ALL','ANY')),
    -- review fix c-3: a permanently-failed dependency has a terminal state, not 'pending' forever
    CHECK (dependency_state IN ('pending','satisfied','conflicted','failed'))
);

-- review fix a-1: single-occupancy projection so Work survives runtime loss without ambiguity
CREATE TABLE IF NOT EXISTS runtime_work_occupancy (
    work_ref    text PRIMARY KEY,
    runtime_ref text NOT NULL,
    since       timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);
