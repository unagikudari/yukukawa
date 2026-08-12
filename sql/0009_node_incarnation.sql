-- Kawa step 8 (v0.5 §13; issue #111 rev 2 + r2 binding constraints BC-1..BC-4).
--
-- Two security-plane tables (OUTSIDE the Domain event log, NOT reduced, NOT in rebuild()'s
-- TRUNCATE set) and one payload column:
--
--   security_fork_evidence   durable fork/equivocation evidence + the origin freeze (8B).
--                            Admission consults it fail-closed; freeze survives restart;
--                            release ONLY via the explicit operator action resolve_fork.
--   result_occurrence_quarantine
--                            BC-2 duplicate containment: a ResultRecorded whose occurrence_key
--                            was already consumed for the same Work is admitted to the log
--                            (append-only cannot refuse history) but recorded HERE and left
--                            inert for projections — no current_work / dependency change.
--
--   event_result.occurrence_key
--                            8D: the plan-node attempt-lineage key (BC-1), a PAYLOAD field —
--                            deliberately NOT an envelope column and NOT an events hash input.

CREATE TABLE IF NOT EXISTS security_fork_evidence (
    origin_node    text        NOT NULL,
    origin_seq     bigint      NOT NULL,          -- the contested position
    held_event_id  text        NOT NULL,          -- the branch this node already holds
    held_hash      text        NOT NULL,
    rival_event_id text        NOT NULL,          -- the authenticated rival successor
    rival_hash     text        NOT NULL,
    held_key_ref   text,                          -- signing keys, for BC-3 scoped revocation
    rival_key_ref  text,
    held_incarnation  text,                       -- key↦incarnation attribution (8A)
    rival_incarnation text,
    classification text        NOT NULL CHECK (classification IN ('equivocation', 'restore_fork')),
    frozen         boolean     NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    resolved_by    text,                          -- operator_ref; NULL while frozen
    resolved_at    timestamptz,
    chosen_head    text,                          -- the surviving branch head event_id
    reason         text,
    PRIMARY KEY (origin_node, origin_seq, rival_hash)
);

CREATE TABLE IF NOT EXISTS result_occurrence_quarantine (
    event_id        text        PRIMARY KEY,      -- the duplicate ResultRecorded event
    work_ref        text        NOT NULL,
    occurrence_key  text        NOT NULL,
    first_event_id  text        NOT NULL,         -- the Result that consumed the key first
    recorded_at     timestamptz NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE event_result ADD COLUMN IF NOT EXISTS occurrence_key text;

-- deterministic duplicate detection needs the consumed-key lookup to be cheap and unique per work
CREATE INDEX IF NOT EXISTS event_result_occurrence_idx
    ON event_result (work_ref, occurrence_key) WHERE occurrence_key IS NOT NULL;
