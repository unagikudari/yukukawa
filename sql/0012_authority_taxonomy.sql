-- Kawa step 10 PR 1 (#118 r2 split; slab §4/§5): typed payload tables for the two
-- authority Domain event kinds. Authority is EVENT-SOURCED — configurations and Receipts
-- replicate, replay, and chain like all history (the r1 (a) resolution: no non-replicated
-- side channel). Append-only like every payload table.

-- the kind whitelist widens with the vocabulary SoT (this gate's reviewed change)
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_kind_check;
ALTER TABLE events ADD CONSTRAINT events_kind_check CHECK (kind IN (
    'plan.created', 'plan.lifecycle_changed', 'work.derived', 'work.dependency_declared',
    'result.recorded', 'link.asserted', 'observation.recorded', 'claim.recorded',
    'work.retired', 'authority.configuration', 'authority.receipt'
));

CREATE TABLE IF NOT EXISTS event_authority_configuration (
    event_id                   text PRIMARY KEY,
    authority_key              text   NOT NULL,
    configuration_digest       text   NOT NULL,
    authority_epoch            bigint NOT NULL,
    members                    text   NOT NULL,   -- canonical-json [key_ref]
    quorum                     int    NOT NULL,
    prior_configuration_digest text,              -- NULL = genesis (epoch 0)
    succession_proof           text               -- canonical-json {signer_set, signatures}
);

CREATE TABLE IF NOT EXISTS event_authority_receipt (
    event_id                       text PRIMARY KEY,
    authority_key                  text   NOT NULL,
    operation_digest               text   NOT NULL,
    configuration_digest           text   NOT NULL,
    authority_epoch                bigint NOT NULL,
    prior_authority_receipt_digest text,
    policy_digest                  text,
    quorum_proof                   text   NOT NULL
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['event_authority_configuration', 'event_authority_receipt'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', 'append_only_' || t, t);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION kawa_forbid_mutation()',
            'append_only_' || t, t
        );
    END LOOP;
END $$;
