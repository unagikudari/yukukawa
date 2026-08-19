-- 0031 — an endpoint's scope and its ordering components are one fact, not four
--
-- The leaf query orders by `{endpoint}_hlc_phys DESC` and the index is built the same
-- way, so Postgres reads it NULLS FIRST. `hlc_parts_sort_key` puts an absent endpoint
-- LAST, because an edge whose far end this node does not hold should not lead the
-- expansion. Measured, the two engines disagree on exactly that row.
--
-- Today the disagreement is unreachable: the leaf requires `{endpoint}_scope_ref = %s`,
-- and every write path fills the scope and the three ordering components in the same
-- statement, so a row with a scope but no stamp does not exist. That is an ARGUMENT
-- about the current writers, and the whole point of #214 step 1 is that the ordering
-- must not depend on one. Adding `NULLS LAST` to the query would stop it matching the
-- index and bring back the Sort that #222 removed.
--
-- So the coupling becomes a fact the database enforces. A future migration that fills
-- one without the other fails here instead of silently ordering one endpoint first on
-- Postgres and last in Python.

ALTER TABLE event_links
    ADD CONSTRAINT event_links_source_endpoint_is_one_fact CHECK (
        (source_scope_ref IS NULL) = (source_hlc_phys IS NULL)
        AND (source_hlc_phys IS NULL) = (source_hlc_logical IS NULL)
        AND (source_hlc_phys IS NULL) = (source_origin_node IS NULL)),
    ADD CONSTRAINT event_links_target_endpoint_is_one_fact CHECK (
        (target_hlc_phys IS NULL) = (target_hlc_logical IS NULL)
        AND (target_hlc_phys IS NULL) = (target_origin_node IS NULL)
        -- `target_scope_ref` is NOT NULL (it carries the `$public` sentinel even for
        -- an unresolved link), so it is not part of this coupling; `resolved` is what
        -- says whether the stamp is there.
        AND (resolved OR target_hlc_phys IS NULL));
