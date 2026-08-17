-- Console phase 1 (#181 rev 3, step 1): the three projection tables the
-- Situation / Fleet / Evidence screens read. TYPED columns, no JSONB bag
-- (#57 §6). These are DISPOSABLE read models — drop-and-rebuild changes no
-- truth. Reducers land in step 2; this migration only fixes the contract.
--
-- Epistemic ground rules (#181 rev 2) are enforced IN SCHEMA where possible:
--   * absence-of-evidence /= negative evidence: UNKNOWN is a stored state,
--     never a NULL-means-whatever or a defaulted zero.
--   * INCOMPLETE always carries its named gap (CHECK below).
--   * STALE is a RENDER-derived state (reachability CRIT => sibling cells
--     render STALE) and is deliberately NOT storable — the CHECKs exclude it.

-- ---- situation_rollup: one row per dimension card (view over other
-- ---- projections; holds no truth of its own; never merged) ----
CREATE TABLE IF NOT EXISTS situation_rollup (
    dimension       text PRIMARY KEY
                    CHECK (dimension IN ('AUTHORITY','EXECUTION','EPISTEMIC',
                                         'HEALTH_REACH','PROJECTION_FRESHNESS')),
    state           text NOT NULL
                    CHECK (state IN ('OK','ATTENTION','CRIT','UNKNOWN','INCOMPLETE')),
    -- INCOMPLETE is first-class and must say WHY (named gap), never a spinner.
    -- gap belongs to INCOMPLETE ONLY: UNKNOWN is absence-of-evidence and may
    -- not carry a named gap (that would blur the two states — review fix).
    gap             text,
    CHECK (state <> 'INCOMPLETE' OR (gap IS NOT NULL AND gap <> '')),
    CHECK (gap IS NULL OR state = 'INCOMPLETE'),
    -- counts always travel WITH their non-green residue (screen-map MUST):
    count_total     integer CHECK (count_total >= 0),
    count_nongreen  integer CHECK (count_nongreen >= 0),
    CHECK (count_nongreen IS NULL OR count_total IS NULL OR count_nongreen <= count_total),
    residue         text CHECK (residue <> ''),   -- listing of the non-green items
    CHECK (count_nongreen IS NULL OR count_nongreen = 0 OR residue IS NOT NULL),
    -- per-card freshness (invariant 7): where this card's numbers came from
    source_projection text NOT NULL,
    as_of           timestamptz NOT NULL
);

-- ---- fleet_node: one row per node; one MEASURED state + its own as_of per
-- ---- dimension cell. online /= workload healthy /= replication current. ----
CREATE TABLE IF NOT EXISTS fleet_node (
    node_ref            text PRIMARY KEY,
    -- reachability: NO live event source exists yet (measured 2026-08-17:
    -- the log carries no reachability kind) => reducers may only ever write
    -- UNKNOWN until a real source event exists. OK/CRIT are in the CHECK so
    -- the contract survives that source landing, but nothing may invent them.
    reachability        text NOT NULL DEFAULT 'UNKNOWN'
                        CHECK (reachability IN ('OK','CRIT','UNKNOWN')),
    reachability_source text,          -- named source event/predicate, NULL iff UNKNOWN
    reachability_as_of  timestamptz,
    CHECK ((reachability = 'UNKNOWN') = (reachability_source IS NULL)),
    CHECK (reachability = 'UNKNOWN' OR reachability_as_of IS NOT NULL),
    -- workload-health: sourced from runtime_work_occupancy + work_dispatch
    workload            text NOT NULL DEFAULT 'UNKNOWN'
                        CHECK (workload IN ('OK','ATTENTION','CRIT','UNKNOWN')),
    workload_detail     text,
    workload_as_of      timestamptz,
    CHECK (workload = 'UNKNOWN' OR workload_as_of IS NOT NULL),
    -- replication-currency: per-origin head vs this node's view; with no
    -- registered replica the honest state is UNKNOWN, never CURRENT.
    replication         text NOT NULL DEFAULT 'UNKNOWN'
                        CHECK (replication IN ('CURRENT','LAGGING','UNKNOWN')),
    -- lag is meaningful ONLY while LAGGING (review fix): LAGGING requires a
    -- positive lag; CURRENT/UNKNOWN must not carry one.
    replication_lag     bigint CHECK (replication_lag > 0),
    CHECK ((replication = 'LAGGING') = (replication_lag IS NOT NULL)),
    replication_as_of   timestamptz,
    CHECK (replication = 'UNKNOWN' OR replication_as_of IS NOT NULL),
    -- last event this node originated (identity/liveness hint, not health)
    last_origin_seq     bigint,
    last_event_at       timestamptz,
    updated_at          timestamptz NOT NULL DEFAULT clock_timestamp()
    -- NOTE deliberately ABSENT: attestation-strength (Authority/Proof concern,
    -- deferred with the verifier read-model — #181 rev 2), and any STALE state
    -- (render-derived from reachability CRIT, never stored).
);

-- ---- evidence_provenance: the Evidence screen's ONLY read surface.
-- ---- 1:1 projection of typed link.asserted events (event-derived ONLY in
-- ---- phase 1); the Console never queries raw event tables. ----
CREATE TABLE IF NOT EXISTS evidence_provenance (
    link_event_id   text PRIMARY KEY,     -- the link.asserted event
    source_ref      text NOT NULL,        -- linking event/subject
    relation        text NOT NULL
                    CHECK (relation IN ('supports','contradicts','based_on','reason_for',
                                        'addresses','reviews','corrects','resolves',
                                        'supersedes','caused_by','satisfies')),
    target_ref      text NOT NULL,
    provenance      text NOT NULL DEFAULT 'event_derived'
                    CHECK (provenance = 'event_derived'),
                    -- 'inferred' is deliberately NOT admissible: no inference
                    -- rules exist; widening this CHECK is the Graph-phase
                    -- change and must arrive WITH the named rule column.
    recorded_at     timestamptz NOT NULL,
    actor_ref       text NOT NULL
);

CREATE INDEX IF NOT EXISTS evidence_provenance_source_idx ON evidence_provenance (source_ref);
CREATE INDEX IF NOT EXISTS evidence_provenance_target_idx ON evidence_provenance (target_ref);
