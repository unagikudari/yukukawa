-- 0029 — the resolution guard has no jurisdiction over GENERATED columns
--
-- 0026 widened the guard to "the resolution transition OR filling columns that are
-- still NULL", implemented as a whole-row jsonb comparison so that a column added
-- later is covered without anyone remembering to name it. That generality is right,
-- and it collides with generated columns for a reason that is not obvious:
--
--   inside a BEFORE trigger, a generated column has NOT been computed yet, so
--   NEW.<generated> is NULL while OLD.<generated> holds the stored value
--
-- The comparison therefore reads every UPDATE as erasing that column, and refuses it.
-- sql/0028 hit this immediately: filling a still-NULL `hlc_logical` on a resolved row
-- -- the exact transition 0026 exists to permit -- began failing the moment
-- `relation_rank` was added.
--
-- The fix is not to special-case `relation_rank`. A generated column cannot be written
-- by anyone, in any statement, ever: Postgres rejects the attempt itself. It is
-- already immutable under a stronger guarantee than this trigger can offer, so the
-- guard should decline jurisdiction over the whole class rather than re-deriving a
-- weaker version of a rule the database already enforces.
--
-- The exclusion is read from the catalog, not from a list, so a generated column added
-- in some later migration is covered on the day it lands.

CREATE OR REPLACE FUNCTION kawa_link_resolution_guard() RETURNS trigger AS $$
DECLARE rewrote_a_set_column boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'append-only: DELETE on event_links is forbidden (emit-enforcement)';
    END IF;

    IF NEW.source_ref IS DISTINCT FROM OLD.source_ref
       OR NEW.relation IS DISTINCT FROM OLD.relation
       OR NEW.target_ref IS DISTINCT FROM OLD.target_ref
       OR NEW.asserted_by_event_id IS DISTINCT FROM OLD.asserted_by_event_id THEN
        RAISE EXCEPTION 'append-only: the link assertion is immutable (emit-enforcement)';
    END IF;

    IF OLD.resolved = false AND NEW.resolved = true THEN
        RETURN NEW;
    END IF;

    IF NEW.resolved IS DISTINCT FROM OLD.resolved THEN
        RAISE EXCEPTION 'append-only: resolved may only go false->true (emit-enforcement)';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM jsonb_each(to_jsonb(OLD)) o
        JOIN jsonb_each(to_jsonb(NEW)) n ON n.key = o.key
        WHERE o.value IS DISTINCT FROM n.value AND o.value <> 'null'::jsonb
          AND o.key NOT IN (SELECT a.attname FROM pg_attribute a
                            WHERE a.attrelid = 'event_links'::regclass
                              AND a.attgenerated <> '')
    ) INTO rewrote_a_set_column;
    IF rewrote_a_set_column THEN
        RAISE EXCEPTION 'append-only: UPDATE on event_links may only fill columns that '
                        'are still NULL (emit-enforcement)';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
