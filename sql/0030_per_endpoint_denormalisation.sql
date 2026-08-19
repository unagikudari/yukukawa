-- 0030 — a link has TWO endpoints, and traversal uses both
--
-- sql/0026 denormalised "the endpoint scope" onto the link row and backfilled it from
-- `target_ref`. That reads as complete until you notice the traversal is bidirectional:
--
--     out-edge:  at `source_ref`, stepping TO `target_ref`  -> target's scope governs
--     in-edge:   at `target_ref`, stepping TO `source_ref`  -> SOURCE's scope governs
--
-- The single `scope_ref` column carried the target's scope in both cases, so the
-- in-direction leaf authorised the step by the scope of the node it was ALREADY AT.
-- A restricted event pointing at a public one was therefore reachable by anyone who
-- could see the public one -- caught by test_scope_evidence_and_neighborhood_withhold-
-- _without_leaking_refs, which is the reason that test names refs instead of counting.
--
-- The ordering columns had the same defect for the same reason: an in-edge ranked by
-- the target's hlc ranks every candidate at that node identically, since they all
-- share the target. The pre-0026 code was right about both -- it joined `events` on
-- `source_ref` for the in-direction and on `target_ref` for the out-direction -- and
-- the denormalisation flattened that distinction away.
--
-- So: one set of columns per endpoint. The target set keeps NOT NULL (an out-leaf also
-- requires `resolved`, which is exactly "the target is held"). The source set is
-- NULLABLE ON PURPOSE: a link may be asserted before its source arrives, and a NULL
-- scope matches no equality predicate, so an in-leaf simply cannot step to an event
-- this node does not hold. That is the correct answer, enforced by the type rather
-- than by a check someone has to remember to write.

ALTER TABLE event_links RENAME COLUMN scope_ref   TO target_scope_ref;
ALTER TABLE event_links RENAME COLUMN hlc_phys    TO target_hlc_phys;
ALTER TABLE event_links RENAME COLUMN hlc_logical TO target_hlc_logical;
ALTER TABLE event_links RENAME COLUMN origin_node TO target_origin_node;

ALTER TABLE event_links
    ADD COLUMN IF NOT EXISTS source_scope_ref   text,
    ADD COLUMN IF NOT EXISTS source_hlc_phys    bigint,
    ADD COLUMN IF NOT EXISTS source_hlc_logical bigint,
    ADD COLUMN IF NOT EXISTS source_origin_node text;

UPDATE event_links l SET
    source_scope_ref   = COALESCE(e.scope_ref, '$public'),
    source_hlc_phys    = split_part(e.hlc, '.', 1)::bigint,   -- hlc-order:allow
    source_hlc_logical = split_part(e.hlc, '.', 2)::bigint,   -- hlc-order:allow
    source_origin_node = e.origin_node
FROM events e WHERE e.event_id = l.source_ref;

-- 0028's indexes name the old columns; rebuild both directions per endpoint.
DROP INDEX IF EXISTS event_links_out_ranked_idx;
DROP INDEX IF EXISTS event_links_in_ranked_idx;

CREATE INDEX event_links_out_ranked_idx ON event_links (
    source_ref, resolved, target_scope_ref, asserter_scope_ref,
    relation_rank, target_hlc_phys DESC, target_hlc_logical DESC,
    target_origin_node COLLATE "C" DESC, target_ref COLLATE "C" DESC);

CREATE INDEX event_links_in_ranked_idx ON event_links (
    target_ref, resolved, source_scope_ref, asserter_scope_ref,
    relation_rank, source_hlc_phys DESC, source_hlc_logical DESC,
    source_origin_node COLLATE "C" DESC, source_ref COLLATE "C" DESC);
