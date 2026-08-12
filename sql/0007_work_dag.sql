-- Kawa step 4 (v0.5 §6-§8, issue #102 rev 2 + round-2 binding constraints).
--
-- Additive only. Structured-intent columns are NULLABLE — absent stays absent (the
-- set-only payload serializer keeps old events' payload_digest byte-identical).
-- work.retired: the third terminal — neither success nor failure; an attributed,
-- intentional withdrawal (#93). No data rewrite.

-- ---- envelope kind vocabulary ----

DO $$
DECLARE conname text;
BEGIN
    SELECT c.conname INTO conname FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    WHERE t.relname = 'events' AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%kind%';
    IF conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE events DROP CONSTRAINT %I', conname);
    END IF;
END $$;
ALTER TABLE events ADD CONSTRAINT events_kind_check CHECK (kind IN (
    'plan.created', 'plan.lifecycle_changed', 'work.derived', 'work.dependency_declared',
    'result.recorded', 'link.asserted', 'observation.recorded', 'claim.recorded',
    'work.retired'
));

-- ---- structured intent (§6.1), additive ----

ALTER TABLE event_plan
    ADD COLUMN IF NOT EXISTS scope text,                     -- display/summary; NON-semantic (#102 §6)
    ADD COLUMN IF NOT EXISTS constraints text[],
    ADD COLUMN IF NOT EXISTS expected_observations text[];   -- deliberately not expected_result (§6.1)

ALTER TABLE event_work
    ADD COLUMN IF NOT EXISTS objective text,
    ADD COLUMN IF NOT EXISTS constraints text[],
    ADD COLUMN IF NOT EXISTS expected_observations text[];

-- ---- work.retired payload (durable, append-only) ----

CREATE TABLE IF NOT EXISTS event_work_retired (
    event_id text PRIMARY KEY REFERENCES events(event_id),
    work_ref text NOT NULL,
    reason   text NOT NULL,
    note     text,
    CHECK (reason IN ('superseded', 'cancelled', 'obsolete'))
);

DO $$
BEGIN
    EXECUTE 'DROP TRIGGER IF EXISTS append_only_event_work_retired ON event_work_retired';
    EXECUTE 'CREATE TRIGGER append_only_event_work_retired BEFORE UPDATE OR DELETE ON '
            'event_work_retired FOR EACH ROW EXECUTE FUNCTION kawa_forbid_mutation()';
END $$;

-- ---- projections learn the third terminal ----

DO $$
DECLARE conname text;
BEGIN
    SELECT c.conname INTO conname FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    WHERE t.relname = 'current_work' AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%execution%';
    IF conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE current_work DROP CONSTRAINT %I', conname);
    END IF;
END $$;
ALTER TABLE current_work ADD CONSTRAINT current_work_execution_check CHECK (execution IN (
    'idle', 'ready', 'executing', 'retryable', 'blocked', 'execution_unknown',
    'result_recorded', 'finished', 'retired'
));

DO $$
DECLARE conname text;
BEGIN
    SELECT c.conname INTO conname FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    WHERE t.relname = 'current_work_dependency' AND c.contype = 'c'
      AND pg_get_constraintdef(c.oid) LIKE '%dependency_state%';
    IF conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE current_work_dependency DROP CONSTRAINT %I', conname);
    END IF;
END $$;
ALTER TABLE current_work_dependency ADD CONSTRAINT current_work_dependency_state_check
    CHECK (dependency_state IN ('pending', 'satisfied', 'failed', 'conflicted', 'retired'));
