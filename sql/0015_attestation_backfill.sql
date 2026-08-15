-- Step 12B (#129 deviation review, finding 3) — custodian attestation backfill.
--
-- The dogfood log predates sign-at-birth: production emitted unsigned events while
-- the trust gate (correctly) rejects them cross-node. The custodian may attach its
-- attestation to events it already holds. This is a CUSTODY column change, never an
-- identity change: signature/signing_key_ref/signature_scheme are outside event_hash
-- (emit.py: "Signature is NOT identity ... NOT fed back into the hash"), so the
-- chain is untouched.
--
-- The trigger now permits exactly TWO monotone transitions, each one-way:
--   1. stub upgrade:        materialized false -> true (+ scope_ref NULL -> value)   [#113 9a]
--   2. attestation backfill: signature NULL -> value (all three provenance columns
--      arrive together; a present signature is NEVER replaced — re-signing is forbidden)
-- Everything else, and every DELETE, stays forbidden.

CREATE OR REPLACE FUNCTION kawa_forbid_mutation_events() RETURNS trigger AS $$
DECLARE
    identity_fixed boolean;
BEGIN
    identity_fixed := TG_OP = 'UPDATE'
       AND NEW.event_id = OLD.event_id AND NEW.origin_node = OLD.origin_node
       AND NEW.origin_seq = OLD.origin_seq AND NEW.hlc = OLD.hlc AND NEW.kind = OLD.kind
       AND NEW.subject_ref IS NOT DISTINCT FROM OLD.subject_ref
       AND NEW.actor_ref = OLD.actor_ref
       AND NEW.policy_digest IS NOT DISTINCT FROM OLD.policy_digest
       AND NEW.payload_digest = OLD.payload_digest
       AND NEW.prev_hash IS NOT DISTINCT FROM OLD.prev_hash
       AND NEW.self_hash = OLD.self_hash
       AND NEW.envelope_version = OLD.envelope_version
       AND NEW.scope_digest IS NOT DISTINCT FROM OLD.scope_digest;

    -- 1. the monotone stub upgrade (#113 9a): provenance columns untouched
    IF identity_fixed
       AND OLD.materialized = false AND NEW.materialized = true
       AND OLD.scope_ref IS NULL
       AND NEW.signature IS NOT DISTINCT FROM OLD.signature
       AND NEW.signing_key_ref IS NOT DISTINCT FROM OLD.signing_key_ref
       AND NEW.signature_scheme IS NOT DISTINCT FROM OLD.signature_scheme
    THEN
        RETURN NEW;
    END IF;

    -- 2. the monotone attestation backfill (#129 12B): materialization untouched
    IF identity_fixed
       AND NEW.materialized = OLD.materialized
       AND NEW.scope_ref IS NOT DISTINCT FROM OLD.scope_ref
       AND OLD.signature IS NULL AND NEW.signature IS NOT NULL
       AND OLD.signing_key_ref IS NULL AND NEW.signing_key_ref IS NOT NULL
       AND OLD.signature_scheme IS NULL AND NEW.signature_scheme IS NOT NULL
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'append-only: % on % is forbidden (emit-enforcement; only the monotone stub upgrade and the monotone attestation backfill may UPDATE)', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
