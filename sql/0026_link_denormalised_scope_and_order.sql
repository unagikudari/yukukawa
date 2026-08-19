-- Kawa — bounded candidate expansion needs the authorization predicate and the
-- ordering key ON the link row (#214 step 4).
--
-- The trilemma #214 rev 3 arrived at, restated because this migration is what
-- resolves it:
--
--     (a) the database work is bounded
--     (b) authorization is applied BEFORE the candidate limit
--     (c) authorization state lives on a table we must JOIN to
--
-- (b) cannot give: dropping it lets invisible rows push visible ones out of the
-- window, so the result depends on what the viewer may not see. (a) cannot give:
-- it is the point. So (c) gives, and the scope comes to the link row.
--
-- Safe for a specific, checkable reason rather than by convention: `scope_ref` is
-- part of the sealed envelope and therefore IMMUTABLE, so a copy here can never
-- go stale. `event_links` is already append-only, and 0024 permits exactly one
-- one-way transition per row, so these are written once and never touched again.
--
-- The HLC components come along for the same reason the collation did in 0025: a
-- candidate window must be selected in the exact order the ranking uses, and
-- `split_part(hlc,...)` on a joined `events` row cannot be indexed here.  hlc-order:allow
--
-- Sentinel, not NULL: `(scope_ref IS NULL OR scope_ref = ANY(...))` forces a
-- BitmapOr and a Sort, and `COALESCE(scope_ref, sentinel)` drops the column out
-- of the index condition entirely (both measured, #214 rev 4 / rev 7). A stored
-- NOT NULL value keeps the predicate plain equality, which is what seeks.
--
-- The sentinel is projection-only. Raw `events.scope_ref` stays NULL for v1
-- envelopes forever: `Event.verify()` structurally forbids a v1 envelope from
-- carrying a scope, so writing one there would make every legacy event fail its
-- own verification and be rejected by replication peers (#214 rev 5).
--
-- Staged deliberately (#214 round 7): ADD COLUMN with a DEFAULT, then backfill,
-- then NOT NULL — rather than one statement that rewrites the table under a long
-- ACCESS EXCLUSIVE lock.

-- 0. widen the append-only guard from "the resolution transition" to the SHAPE it
--    was written for. 0024 permits `resolved` false->true and, as a consequence of
--    that being one-way, whatever else the same statement sets. A backfill is the
--    other one-shot write of derived state: it fills columns that are still NULL
--    without touching `resolved` at all -- and 0024 refused it, which is how this
--    migration first failed against the dogfood database.
--
--    Both stay one-shot by construction: resolution requires resolved = false, and
--    a NULL -> value fill requires the OLD value to be NULL. The assertion columns
--    remain immutable under either.
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
    ) INTO rewrote_a_set_column;
    IF rewrote_a_set_column THEN
        RAISE EXCEPTION 'append-only: UPDATE on event_links may only fill columns that '
                        'are still NULL (emit-enforcement)';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 1. add NULLABLE and WITHOUT a default. A `NOT NULL DEFAULT` fills every existing
--    row with the sentinel immediately, which turns the backfill below into a
--    rewrite of an already-set column -- and the guard above correctly refuses it.
--    (Measured: that is exactly how the first cut of this migration failed.) The
--    default and NOT NULL go on in step 3, after the values are in place.
ALTER TABLE event_links
    ADD COLUMN IF NOT EXISTS scope_ref          text,
    ADD COLUMN IF NOT EXISTS asserter_scope_ref text,
    ADD COLUMN IF NOT EXISTS hlc_phys           bigint,
    ADD COLUMN IF NOT EXISTS hlc_logical        bigint,
    ADD COLUMN IF NOT EXISTS origin_node        text;

-- 2. backfill from the endpoint and the asserting event. Unresolved links have no
--    endpoint held locally, so their ordering columns stay NULL until resolution
--    fills them on 0024's one-way transition.
UPDATE event_links l SET
    scope_ref   = COALESCE(e.scope_ref, '$public'),
    -- the backfill of the very columns the helper reads: DDL/DML cannot call it,
    -- so this is the deliberate duplication, marked per line  hlc-order:allow
    hlc_phys    = split_part(e.hlc, '.', 1)::bigint,   -- hlc-order:allow
    hlc_logical = split_part(e.hlc, '.', 2)::bigint,   -- hlc-order:allow
    origin_node = e.origin_node
FROM events e
WHERE e.event_id = l.target_ref AND l.resolved;

UPDATE event_links l SET asserter_scope_ref = COALESCE(le.scope_ref, '$public')
FROM events le WHERE le.event_id = l.asserted_by_event_id;

-- 3. anything the backfill could not reach is public by definition (an unresolved
--    link has no endpoint held here yet), then the column becomes NOT NULL so the
--    predicate stays plain equality forever.
UPDATE event_links SET scope_ref = '$public' WHERE scope_ref IS NULL;
UPDATE event_links SET asserter_scope_ref = '$public' WHERE asserter_scope_ref IS NULL;

ALTER TABLE event_links
    ALTER COLUMN scope_ref          SET DEFAULT '$public',
    ALTER COLUMN scope_ref          SET NOT NULL,
    ALTER COLUMN asserter_scope_ref SET DEFAULT '$public',
    ALTER COLUMN asserter_scope_ref SET NOT NULL;

-- 4. the indexes the bounded windows seek on. BOTH directions: traversal expands
--    outgoing (source_ref) and incoming (target_ref), and an index for one leaves
--    half of every traversal unindexed — bounded on the way out, unbounded back.
--    Column order is the leaf query's: equality keys, then the ordering triple.
--    Collation goes on the ORDERING text columns only. An index that also pins it
--    on the equality columns is not matched by a plain `source_ref = $1`, and the
--    planner falls back to another index plus a Sort — measured on a 200k-row probe,
--    where this geometry gives `Limit -> Index Only Scan` with every equality key in
--    the Index Cond and no Sort at all.
CREATE INDEX IF NOT EXISTS event_links_out_causal_idx ON event_links (
    source_ref, relation, resolved, scope_ref,
    hlc_phys DESC, hlc_logical DESC, origin_node COLLATE "C" DESC, target_ref COLLATE "C" DESC
);

CREATE INDEX IF NOT EXISTS event_links_in_causal_idx ON event_links (
    target_ref, relation, resolved, scope_ref,
    hlc_phys DESC, hlc_logical DESC, origin_node COLLATE "C" DESC, source_ref COLLATE "C" DESC
);
