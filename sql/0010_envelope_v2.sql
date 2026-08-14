-- Kawa step 9a (v0.5 §12; issue #113 rev 2 — envelope v2 + stubs).
--
-- Additive only (no rewrite of any existing row's hash inputs):
--   envelope_version   which hash preimage this envelope verifies under (DEFAULT 1 covers
--                      every pre-step-9 row exactly — the version is explicit, never inferred
--                      from column absence at the model layer).
--   scope_digest       what the v2 preimage commits to (sha256 of scope_ref); NULL = unscoped.
--   scope_ref          v2 cleartext scope, present only when this node may see it; admission
--                      integrity-checks it against scope_digest. Stubs and withheld events
--                      keep it NULL.
--   materialized       false = stub: an origin-signed envelope whose payload bytes this node
--                      does not hold. Stubs chain and replicate but are reducer-inert; the
--                      ONE predicate (materialized) gates reduction in admission AND rebuild.

ALTER TABLE events ADD COLUMN IF NOT EXISTS envelope_version smallint NOT NULL DEFAULT 1;
ALTER TABLE events ADD COLUMN IF NOT EXISTS scope_digest text;
ALTER TABLE events ADD COLUMN IF NOT EXISTS scope_ref text;
ALTER TABLE events ADD COLUMN IF NOT EXISTS materialized boolean NOT NULL DEFAULT true;

-- a v1 envelope never carries a scope (the structural downgrade guard, mirrored in code)
ALTER TABLE events ADD CONSTRAINT events_v1_scopeless
    CHECK (envelope_version > 1 OR (scope_digest IS NULL AND scope_ref IS NULL));

-- The append-only guard narrows, deliberately, for exactly ONE transition: stub upgrade.
-- The ENVELOPE (every hash-relevant column, provenance, position) stays immutable forever;
-- what may change is node-LOCAL materialization state, monotonically:
--   materialized  false -> true only (never back to stub)
--   scope_ref     NULL -> value only (the cleartext arrives WITH the bytes; never changes after)
-- Any other UPDATE, and every DELETE, stays forbidden. History is never rewritten — a node
-- only ever learns bytes it had already committed to by hash.
CREATE OR REPLACE FUNCTION kawa_forbid_mutation_events() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.materialized = false AND NEW.materialized = true
       AND OLD.scope_ref IS NULL
       AND NEW.event_id = OLD.event_id AND NEW.origin_node = OLD.origin_node
       AND NEW.origin_seq = OLD.origin_seq AND NEW.hlc = OLD.hlc AND NEW.kind = OLD.kind
       AND NEW.subject_ref IS NOT DISTINCT FROM OLD.subject_ref
       AND NEW.actor_ref = OLD.actor_ref
       AND NEW.policy_digest IS NOT DISTINCT FROM OLD.policy_digest
       AND NEW.payload_digest = OLD.payload_digest
       AND NEW.prev_hash IS NOT DISTINCT FROM OLD.prev_hash
       AND NEW.self_hash = OLD.self_hash
       AND NEW.envelope_version = OLD.envelope_version
       AND NEW.scope_digest IS NOT DISTINCT FROM OLD.scope_digest
       AND NEW.signature IS NOT DISTINCT FROM OLD.signature
       AND NEW.signing_key_ref IS NOT DISTINCT FROM OLD.signing_key_ref
       AND NEW.signature_scheme IS NOT DISTINCT FROM OLD.signature_scheme
    THEN
        RETURN NEW;   -- the one permitted transition: stub -> materialized (#113 9a)
    END IF;
    RAISE EXCEPTION 'append-only: % on % is forbidden (emit-enforcement; only the monotone stub upgrade may UPDATE)', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS append_only_events ON events;
CREATE TRIGGER append_only_events BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION kawa_forbid_mutation_events();
