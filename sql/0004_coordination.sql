-- Kawa Phase 0 — coordination / dispatch bridge (NOT Domain SoT).
--
-- work_dispatch is disposable coordination (#53 §11/§16): it records that a Work was handed to a
-- runtime via a *replaceable* transport (#53 §17). It is mutable and holds NO Authority — the
-- durable truth is the Work (derived identity) and the Result (an Event). The broker is used as a
-- wake transport only; broker_task_id is a coordination hint, never authority.
--
-- The append-only guard (sql/0003) intentionally does NOT cover this table: coordination is
-- meant to be updated. Not a projection either — reducers do not own it; the dispatch adapter does.

CREATE TABLE IF NOT EXISTS work_dispatch (
    work_ref        text PRIMARY KEY,
    context         text NOT NULL,                 -- the request the runtime pulls (transport payload)
    target_agent    text,                          -- eligible runtime/agent (a role hint)
    transport       text NOT NULL DEFAULT 'broker',
    broker_task_id  text,                          -- coordination hint returned by the transport
    dispatch_state  text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (dispatch_state IN ('pending','dispatched','completed','failed'))
);
