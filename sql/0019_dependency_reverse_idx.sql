-- #166 (order-tolerant reducers): the reactive dependency cascade — recompute dependents
-- when a work's folded state changes — walks edges BY DEPENDENCY. Without this reverse
-- index that walk is a full-table scan per touched work, quadratic under batch replay
-- (review 6bea592c finding 4). Forward lookups ride the existing primary key.
CREATE INDEX IF NOT EXISTS current_work_dependency_reverse_idx
    ON current_work_dependency (dependency_work_ref);
