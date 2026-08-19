-- Kawa — separate homogeneous from heterogeneous edges physically, so no candidate
-- window can ever be consumed by a row the viewer may not see (#222).
--
-- Edge visibility is an AND over two independent scopes (#146 F4): the endpoint's and
-- the asserting event's. 0026 indexed them unconditionally, which leaves the asserter
-- dimension as a residual filter — and #222's review showed that is not a completeness
-- gap but an exploitable one:
--
--     target    a widely-referenced public node          (scope_ref = '$public')
--     attacker  emits 1,000 edges toward it from an isolated scope only it can read
--               (scope_ref = '$public', asserter_scope_ref = 'secret_attacker')
--
--     an ordinary viewer holding {'$public'} expands that node:
--       the candidate window fills with the attacker's edges
--       the asserter filter drops all of them
--       result: zero edges
--
-- A low-privilege actor erases a high-privilege viewer's sight of the PUBLIC graph,
-- with no privilege escalation and nothing forged. Shadow censorship, not slowness.
--
-- The fix is to name BOTH scopes explicitly in every leaf, so a pair the viewer does
-- not hold is never walked: one leaf per authorized ordered pair (s_endpoint,
-- s_asserter), including the pairs where the two are the same. Every row the scan
-- touches is then a row the viewer may see, and there is no window for an invisible
-- row to consume — the attack has nothing to push against.
--
-- #222 round 2 proposed reaching that through PARTIAL indexes, split on whether the
-- two scopes agree, so the homogeneous case would need only one scope as a key. That
-- structural insight is what this migration implements; its partial-index realisation
-- is not, because it does not survive the planner. Measured on a 200k-row probe:
--
--     WHERE scope_ref = 's1' AND scope_ref = asserter_scope_ref
--       -> Postgres rewrites this by transitivity to
--          scope_ref = 's1' AND asserter_scope_ref = 's1'
--       -> the column-to-column clause the partial index is DEFINED on no longer
--          appears, the implication prover fails, and the index is not matched at all
--
-- A single index carrying both scopes as equality keys serves both cases instead, and
-- measured, both seek cleanly:
--
--     pair (s1, s1)     -> Limit -> Index Only Scan, five equality keys, no Sort
--     pair (s1, other)  -> Limit -> Index Only Scan, five equality keys, no Sort
--
-- Fewer indexes, no predicate for the planner to optimise away, and the same
-- structural property: the leaf names the pair, so nothing invisible is reachable.
--
-- PRECONDITION, load-bearing: both scope columns are NOT NULL (0026). `NULL = NULL`
-- and `NULL <> NULL` are BOTH unknown in SQL, so a NULL in either column would drop
-- the row out of both partial indexes and make it invisible to everyone. A test pins
-- this so relaxing it fails loudly rather than silently deleting rows from every view.
--
-- Collation stays OFF the equality keys: #221 measured that `COLLATE "C"` there stops
-- a plain `col = $1` matching, and the planner falls back to another index plus a Sort.

-- CREATE before DROP, deliberately. Doing it the other way leaves a window with no
-- usable index at all: inside a transaction the CREATE takes a SHARE lock and blocks
-- writes, and outside one every live query falls to a Seq Scan until the build
-- finishes. Overlapping them costs disk for the duration and nothing else.
CREATE INDEX IF NOT EXISTS event_links_out_scoped_idx ON event_links (
    source_ref, relation, resolved, scope_ref, asserter_scope_ref,
    hlc_phys DESC, hlc_logical DESC, origin_node COLLATE "C" DESC, target_ref COLLATE "C" DESC
);

CREATE INDEX IF NOT EXISTS event_links_in_scoped_idx ON event_links (
    target_ref, relation, resolved, scope_ref, asserter_scope_ref,
    hlc_phys DESC, hlc_logical DESC, origin_node COLLATE "C" DESC, source_ref COLLATE "C" DESC
);

DROP INDEX IF EXISTS event_links_out_causal_idx;
DROP INDEX IF EXISTS event_links_in_causal_idx;
