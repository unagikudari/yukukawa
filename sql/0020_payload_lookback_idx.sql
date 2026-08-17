-- #166 review F4: the symmetric fold's lookback queries filter payload tables by
-- work_ref / plan_ref; only (event_id) PKs existed, so every fold was a seq scan and
-- rebuild was O(N^2) asymptotically (measured ~2x at 280 events — fine, but the shape
-- is wrong). These make the fold's fact reads index scans.
CREATE INDEX IF NOT EXISTS event_work_work_ref_idx ON event_work (work_ref);
CREATE INDEX IF NOT EXISTS event_work_retired_work_ref_idx ON event_work_retired (work_ref);
CREATE INDEX IF NOT EXISTS event_result_work_ref_idx ON event_result (work_ref);
CREATE INDEX IF NOT EXISTS event_plan_plan_ref_idx ON event_plan (plan_ref);
