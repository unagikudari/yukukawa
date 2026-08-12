-- Kawa step 2 (v0.5 §2 epistemic nucleus, issue #97 rev 2 + round-2 binding constraints).
--
-- Durable additions: typed payload tables for link.asserted / observation.recorded /
-- claim.recorded (append-only, like every payload table).
--
-- Reclassification: event_links was created in 0001 as a durable table with an append-only
-- trigger, but no writer ever existed and its shape could not express v0.5 §5 (targets are
-- refs, not FK-constrained event ids). It is now what the contract says it is: a
-- reducer-owned PROJECTION over link.asserted events — rebuildable, truncatable, never
-- written outside the reducer. Per review binding constraint 4 this migration recreates it
-- unconditionally (DROP IF EXISTS): the projection is 100% derivable from the event log,
-- so a non-empty table is rebuilt by replay, never a reason to abort.

-- ---- envelope: the kind vocabulary grows by the three epistemic kinds ----

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
    'result.recorded', 'link.asserted', 'observation.recorded', 'claim.recorded'
));

-- ---- typed payload tables (durable, append-only) ----

CREATE TABLE IF NOT EXISTS event_link (
    event_id   text PRIMARY KEY REFERENCES events(event_id),
    source_ref text NOT NULL,
    relation   text NOT NULL,
    target_ref text NOT NULL,
    CHECK (relation IN ('supports','contradicts','based_on','reason_for','addresses',
                        'reviews','corrects','resolves','supersedes','caused_by','satisfies')),
    CHECK (source_ref <> target_ref)                    -- self-link is invalid at the root
);

CREATE TABLE IF NOT EXISTS event_observation (
    event_id                 text PRIMARY KEY REFERENCES events(event_id),
    predicate                text NOT NULL,
    -- exactly ONE value type (physical-schema §9): the gate that keeps free-form JSON out
    value_text               text,
    value_number             double precision,
    value_bool               boolean,
    value_time               text,     -- canonical ISO-8601 string: payload_digest stability
                                       -- outranks SQL typedness (timestamptz round-trips are
                                       -- not byte-stable and would break content addressing)
    observation_method_class text NOT NULL,
    occurred_at              text,     -- time of observation (payload data, never order)
    -- #98 §2 source binding: all-NULL, or a coherent snapshot tuple (constraint 3)
    source_ref               text,
    source_revision          text,
    content_digest           text,
    fetched_at               text,
    CHECK (num_nonnulls(value_text, value_number, value_bool, value_time) = 1),
    CHECK (observation_method_class IN ('command_exit','http_probe','file_digest',
                                        'api_fetch','metric_read','manual_human')),
    CHECK ((source_ref IS NULL AND source_revision IS NULL
            AND content_digest IS NULL AND fetched_at IS NULL)
        OR (source_ref IS NOT NULL AND content_digest IS NOT NULL AND fetched_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS event_claim (
    event_id    text PRIMARY KEY REFERENCES events(event_id),
    proposition text NOT NULL,
    basis_note  text
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['event_link','event_observation','event_claim'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', 'append_only_' || t, t);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION kawa_forbid_mutation()',
            'append_only_' || t, t
        );
    END LOOP;
END $$;

-- ---- event_links: durable table -> reducer-owned projection ----

DROP TRIGGER IF EXISTS append_only_event_links ON event_links;
DROP TABLE IF EXISTS event_links;
CREATE TABLE event_links (
    source_ref           text NOT NULL,
    relation             text NOT NULL,
    target_ref           text NOT NULL,
    resolved             boolean NOT NULL DEFAULT false,  -- target event exists locally
    asserted_by_event_id text NOT NULL,                   -- FIRST asserting link event (dedup keeps it)
    PRIMARY KEY (source_ref, relation, target_ref)
);
CREATE INDEX event_links_unresolved_target_idx ON event_links (target_ref) WHERE NOT resolved;
CREATE INDEX event_links_source_idx ON event_links (source_ref);
CREATE INDEX event_links_target_idx ON event_links (target_ref);

-- ---- derived claim standing (projection; protocol state, never Truth — v0.5 §2.6) ----

CREATE TABLE IF NOT EXISTS current_claim_standing (
    claim_event_id  text PRIMARY KEY,
    standing        text NOT NULL DEFAULT 'unevaluated',
    latest_event_id text NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (standing IN ('grounded_supported','contradicted','contested','superseded','unevaluated'))
);
