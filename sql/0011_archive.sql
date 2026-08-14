-- Kawa step 9c (v0.5 §12.3; issue #113 rev 2 (d)).
--
-- Security-plane evidence of archive custody verification — OUTSIDE the Domain event log,
-- NOT reduced, NOT in rebuild()'s TRUNCATE set, and NEVER counted by the replication
-- frontier: a detached verified segment is archive evidence, not live history. Segment
-- events enter the live store only through normal admission (as chained gap-fill).

CREATE TABLE IF NOT EXISTS security_archive_segment (
    commitment_digest text        PRIMARY KEY,   -- content address of the signed commitment
    origin_node       text        NOT NULL,
    from_seq          bigint      NOT NULL,
    to_seq            bigint      NOT NULL,
    from_hash         text,                      -- prev_hash the segment chains from (NULL = genesis)
    to_hash           text        NOT NULL,      -- self_hash of the segment head
    event_set_digest  text        NOT NULL,      -- digest over the ordered event_ids
    archiver_key_ref  text        NOT NULL,      -- who attested custody+contiguity (NOT authorship)
    path              text,                      -- where the segment file was seen (advisory)
    verified_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
    detached          boolean     NOT NULL DEFAULT false   -- true: verified but not (yet) chained
);
