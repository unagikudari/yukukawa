-- Kawa — deferred link resolution is a legal transition (#215).
--
-- `reducers.reduce` runs a universal backfill on every ingested event:
--
--     UPDATE event_links SET resolved = true WHERE target_ref = %s AND NOT resolved
--
-- and `0003_append_only.sql` forbids every UPDATE on the table. The two have
-- coexisted only because the statement has always matched zero rows — the
-- trigger is FOR EACH ROW, so it never fired. The first genuinely out-of-order
-- cross-origin link would abort the ingestion of the event that resolves it.
--
-- The append-only contract is about the ASSERTION: who linked what to what,
-- and under whose authority. `resolved` is not part of that assertion — it is
-- derived local state recording whether this node holds the endpoint yet. It is
-- the one field that must be allowed to advance, exactly once, in one
-- direction.
--
-- The exception is written for the SHAPE rather than for `resolved` alone, so
-- that #214 step 4 -- which denormalises endpoint scope and HLC ordering
-- components onto this row on the same transition -- lands without a second
-- rewrite of an append-only guard, which is the kind of churn that invites
-- someone to loosen it instead.
--
-- Note precisely what enforces that, because it is a consequence and not a
-- check: the guard does not inspect the other columns at all. It does not need
-- to. Passing requires OLD.resolved = false, and resolution sets it true, so
-- **any row can be updated at most once in its lifetime**. A column populated
-- during that single transition can never be rewritten afterwards, because
-- there is no afterwards. Adding a column later therefore needs no new rule.

-- Columns that ARE the assertion are named explicitly below rather than
-- inferred, so a future column is permitted by default and an assertion field
-- can only become mutable if someone deliberately removes it from this list.

CREATE OR REPLACE FUNCTION kawa_link_resolution_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'append-only: DELETE on event_links is forbidden (emit-enforcement)';
    END IF;

    -- The assertion itself is immutable, always.
    IF NEW.source_ref IS DISTINCT FROM OLD.source_ref
       OR NEW.relation IS DISTINCT FROM OLD.relation
       OR NEW.target_ref IS DISTINCT FROM OLD.target_ref
       OR NEW.asserted_by_event_id IS DISTINCT FROM OLD.asserted_by_event_id THEN
        RAISE EXCEPTION 'append-only: the link assertion is immutable (emit-enforcement)';
    END IF;

    -- One direction, once. false->true is the resolution; anything else --
    -- true->false, or a no-op rewrite -- is not a transition this permits.
    IF NOT (OLD.resolved = false AND NEW.resolved = true) THEN
        RAISE EXCEPTION 'append-only: UPDATE on event_links is forbidden outside '
                        'the false->true resolution (emit-enforcement)';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS append_only_event_links ON event_links;
CREATE TRIGGER append_only_event_links BEFORE UPDATE OR DELETE ON event_links
    FOR EACH ROW EXECUTE FUNCTION kawa_link_resolution_guard();
