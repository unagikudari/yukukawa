-- Phase 4A: attested Event origin. Provenance columns are SEPARATE from identity:
--   event_id = self_hash = content_hash (unchanged). The signature is over content_hash, is NOT the
--   identity, and is NULL for unattested writes. signing_key_ref resolves the historical public key
--   so rotation/revocation does not break verification of past Events. signature_scheme is a
--   mechanics/profile string (e.g. 'ed25519'), deliberately not a constrained Domain enum.
ALTER TABLE events
    ADD COLUMN IF NOT EXISTS signature        text,
    ADD COLUMN IF NOT EXISTS signing_key_ref  text,
    ADD COLUMN IF NOT EXISTS signature_scheme text;
