-- Kawa — the causal-order index must match the collation the queries now ask for
-- (#214 step 1, review round 2).
--
-- `hlc_order_sql` pins `COLLATE "C"` on the text components, because the database
-- default is a locale collation and Python's `sorted` is code point order. Measured
-- on this fleet's ja_JP.UTF-8 database:
--
--     ORDER BY v                 ->  Z e nodeZ ノード Ω é
--     Python sorted(v)           ->  Z e nodeZ é Ω ノード
--     ORDER BY v COLLATE "C"     ->  Z e nodeZ é Ω ノード
--
-- 0016 built `events_hlc_causal_idx` with the default collation on `origin_node`. A
-- b-tree index is only usable for an ORDER BY whose collation MATCHES, so pinning the
-- collation in the query without rebuilding the index turns the brief's index tail
-- read back into the full scan 0016 existed to remove — and would defeat the bounded
-- candidate windows #214 step 4 is built on.
--
-- `event_id` joins the index because it is the unique final component every caller now
-- passes: the three preceding fields are a PREORDER over rows (two events from one
-- origin in the same millisecond with the same logical counter tie), so an index that
-- stops at `origin_node` cannot serve the total order the queries specify.
--
-- An index definition cannot call `hlc_order_sql` — DDL is the one place the ordering
-- MUST be spelled out a second time, which is exactly the drift this step exists to
-- stop. So it is spelled once here and pinned by a test: `test_hlc_order_parity.py`
-- reads this index back from the catalogue and asserts it matches what the helper
-- generates. The pragma below marks the deliberate duplication; the test is what
-- makes it safe.

DROP INDEX IF EXISTS events_hlc_causal_idx;

CREATE INDEX events_hlc_causal_idx ON events (
    (split_part(hlc, '.', 1)::bigint) DESC,   -- hlc-order:allow (pinned by the parity test)
    (split_part(hlc, '.', 2)::bigint) DESC,   -- hlc-order:allow (pinned by the parity test)
    origin_node COLLATE "C" DESC,
    event_id COLLATE "C" DESC
);
