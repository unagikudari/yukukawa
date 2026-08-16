-- Brief freshness lookup (review 1d352c3c, finding 2) — index for the "latest event"
-- read that context.bootstrap runs at every session start.
--
-- hlc is text "<phys_ms>.<logical>.<node>", so lexicographic order is WRONG; the
-- canonical causal order (reducers._LATEST_RESULT_SQL, §3) is the two numeric hlc
-- fields with origin_node as the final tiebreak. This expression index matches that
-- ORDER BY exactly, turning the brief's full-table Seq Scan into an index tail read.
-- recorded_at is never a CROSS-NODE ordering key (local wall-clock + replica
-- arrival time). Since the 2026-08-17 rebuild fix it IS this node's LOCAL replay
-- coordinate (reducers.load_events orders by it) — node-local application order,
-- still never shared truth; the durable/causal coordinate question is #167.
CREATE INDEX IF NOT EXISTS events_hlc_causal_idx ON events (
    (split_part(hlc, '.', 1)::bigint) DESC,
    (split_part(hlc, '.', 2)::bigint) DESC,
    origin_node DESC
);
