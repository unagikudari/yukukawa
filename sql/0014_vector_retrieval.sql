-- 0014: step 11B — vector retrieval substrate (#122 rev 2 (f)-(i); gate GO
-- verdict Observation sha256:e060ca1a…, PR #125).
--
-- Both tables are §12.2 DERIVED MATERIALIZATIONS: rebuildable projections over
-- materialized events, node-local (embeddings are derived — never replicated,
-- per #122 deferred list), never part of Domain truth. TRUNCATE + reindex is
-- always safe.
--
-- CREATE EXTENSION needs superuser and is expected to be pre-installed
-- out-of-band (done via the fleet membroker role); IF NOT EXISTS makes this a
-- no-op on an installed database.

CREATE EXTENSION IF NOT EXISTS vector;

-- The canonical embeddable text of one materialized event, content-addressed.
-- content_digest = sha256 over canonical_text bytes: identical text in two
-- attributed records SHARES a digest (and thus an embedding) while the records
-- never collapse — the no-collapse invariant lives on this split (#122 (g)).
-- Events whose kind carries no semantic bytes (links, dependencies, retire,
-- authority plumbing, objective-less lifecycle) have NO row; they are reported
-- as no_content in the embedding frontier, never silently absent.
-- No FK to events — the projection-table precedent (event_links, current_*):
-- derived materializations are rebuildable and must not couple the event log's
-- lifecycle (TRUNCATE in tests, archive restores) to their own.
CREATE TABLE event_content (
    event_id        text PRIMARY KEY,
    content_digest  text NOT NULL,
    canonical_text  text NOT NULL,
    extracted_at    timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX event_content_digest_idx ON event_content (content_digest);

-- Embeddings keyed by CONTENT identity + model identity — never by event
-- (#122 (g): computed once, reused where safe). model_identity is a canonical
-- digest of the model's *behavior* (see kawa/embeddings.py); changing models
-- re-embeds under a new identity without touching old rows. The column is
-- dimension-untyped on purpose: dimensions are a model-profile property, and
-- at dogfood scale exact scan is honest; an ANN index (fixed-dim) is a later
-- profile concern, reported via the frontier, not silently assumed.
CREATE TABLE content_embedding (
    content_digest  text NOT NULL,
    model_identity  text NOT NULL,
    embedding       vector NOT NULL,
    computed_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (content_digest, model_identity)
);
