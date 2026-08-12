-- Kawa step 5 (v0.5 §14-§15 / #98 §3-§7; issue #104 rev 2 + round-2 constraints).
--
-- The security plane is DELIBERATELY OUTSIDE the Domain event log. These tables are NOT
-- Domain Events (§14: "not every layer is a durable Domain entity"), NOT reduced by any
-- reducer, and NOT in rebuild()'s TRUNCATE set — so a projection rebuild never touches
-- them (asserted by test). They are durable security state: issuance audit, attestations
-- kept for LATER verification, and a local revocation deny-list. The nonce replay cache is
-- intentionally NOT here — it is ephemeral, in-memory, per process (a restart is a new
-- incarnation, §14.1).
--
-- The append-only triggers reuse the Domain guard function but are declared here, in the
-- security plane's own migration, and never added to reducers' Domain table list.

CREATE TABLE IF NOT EXISTS security_credential_issued (
    jti            text PRIMARY KEY,
    sub            text NOT NULL,                 -- workload identity
    node           text,
    runtime        text,
    workload       text,
    cnf_jkt        text NOT NULL,                 -- PoP public-key thumbprint bound at issuance
    iss            text NOT NULL,                 -- issuer id (broker), NOT a node origin key
    iat            text NOT NULL,
    exp            text NOT NULL,
    capability_ctx text,                          -- canonical-json capability/scope claims
    recorded_at    timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS security_attestation (
    jti                  text PRIMARY KEY,
    work_ref             text NOT NULL,
    derived_event_id     text NOT NULL,           -- the immutable WorkDerived event attested
    work_semantics_digest text NOT NULL,          -- digest over that event's canonical Work fields
    source_basis         text,                    -- canonical-json [{source_ref, content_digest}]
    policy_digest        text,
    iss                  text NOT NULL,
    iat                  text NOT NULL,
    exp                  text NOT NULL,
    signature            text NOT NULL,
    recorded_at          timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS security_revocation (
    revoked_ref text PRIMARY KEY,                 -- a jti or a key-id
    kind        text NOT NULL,                    -- 'credential' | 'key'
    reason      text,
    revoked_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (kind IN ('credential', 'key'))
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['security_credential_issued', 'security_attestation', 'security_revocation'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', 'append_only_' || t, t);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION kawa_forbid_mutation()',
            'append_only_' || t, t
        );
    END LOOP;
END $$;
