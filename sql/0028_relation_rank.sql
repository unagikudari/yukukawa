-- Kawa — encode the relation priority as an ORDER, not as a partition (#222 rev 6).
--
-- The candidate leaves must return rows in the global ranking order, which puts
-- relation priority ahead of the HLC. The first cut satisfied that by splitting the
-- query one leaf per relation, which is correct and does not scale: 11 relations x 2
-- directions x |V|^2 pairs. Measured against a table with no matching rows, so this
-- is planning cost alone:
--
--     |V|=1      22 leaves      6.0 ms per expanded node
--     |V|=4     352 leaves     54.9 ms
--     |V|=8   1,408 leaves    243.4 ms
--
-- A depth-2 walk over twenty nodes at |V|=8 spends five seconds before touching data,
-- and that number is a FLOOR: parsing that many UNION ALL branches and initialising
-- that many executor iterators is paid whether or not any row matches.
--
-- The requirement was never "split eleven ways" — it is "relation priority precedes
-- hlc in the traversal order". A rank column carries that into the index, so one seek
-- per (scope pair x direction) returns rows already globally ordered:
--
--     |V|=8     128 leaves      ~20 ms
--
-- Containment is unchanged in form: within a pair leaf the ordering IS the global
-- ranking restricted to that pair, so a row excluded from its leaf's top-K has K rows
-- of the same pair ahead of it globally and cannot be a global winner. What matters is
-- that the order INSIDE a partition agrees with the global order — never the number of
-- partitions.
--
-- The rank is derived from the frozen relation priority (kawa/retrieval._RELATION_GROUPS,
-- the Lock 2 groups). It is written once with the other denormalised columns, on the
-- same one-way transition 0024/0026 permit.

-- A GENERATED column, not one the reducer fills. The rank is a pure function of the
-- relation, so deriving it in the database makes rank and relation structurally unable
-- to disagree -- no write path to forget, no backfill to drift, nothing to keep in
-- step. That matters here specifically: this plan has hit "one concept, two
-- representations" six times, and a rank maintained beside the relation would be the
-- seventh.
ALTER TABLE event_links ADD COLUMN IF NOT EXISTS relation_rank smallint
    GENERATED ALWAYS AS (
        CASE relation
            WHEN 'supersedes'  THEN 0
            WHEN 'contradicts' THEN 1     -- Lock 2 group: supports and contradicts
            WHEN 'supports'    THEN 2     -- share a priority band, ordered by name
            WHEN 'reason_for'  THEN 3
            WHEN 'based_on'    THEN 4
            WHEN 'addresses'   THEN 5
            WHEN 'caused_by'   THEN 6
            WHEN 'corrects'    THEN 7
            WHEN 'resolves'    THEN 8
            WHEN 'reviews'     THEN 9
            WHEN 'satisfies'   THEN 10
            ELSE 99                       -- unknown ranks last, deterministically,
        END                               -- rather than sorting unpredictably
    ) STORED;

-- one leaf per (pair x direction): the rank is an ordering column, not a key
CREATE INDEX IF NOT EXISTS event_links_out_ranked_idx ON event_links (
    source_ref, resolved, scope_ref, asserter_scope_ref,
    relation_rank, hlc_phys DESC, hlc_logical DESC,
    origin_node COLLATE "C" DESC, target_ref COLLATE "C" DESC
);

CREATE INDEX IF NOT EXISTS event_links_in_ranked_idx ON event_links (
    target_ref, resolved, scope_ref, asserter_scope_ref,
    relation_rank, hlc_phys DESC, hlc_logical DESC,
    origin_node COLLATE "C" DESC, source_ref COLLATE "C" DESC
);

DROP INDEX IF EXISTS event_links_out_scoped_idx;
DROP INDEX IF EXISTS event_links_in_scoped_idx;
