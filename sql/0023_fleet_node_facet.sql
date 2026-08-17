-- Fleet telemetry checkpoint 1 (#189 rev 3 §C): the facet projection the
-- Fleet screen's telemetry columns will read. Predicate-generic BY DESIGN:
-- adding a metric is a registry change (kawa/projections/facet_registry.py,
-- reviewed), never a schema change — hence deliberately NO predicate enum here.
-- Epistemic rules inherited from sql/0021: typed columns, exactly one value,
-- honesty in schema.
CREATE TABLE IF NOT EXISTS fleet_node_facet (
    node_ref        text NOT NULL,
    predicate       text NOT NULL,
    -- empty-string sentinel for unqualified predicates: deterministic PK,
    -- no NULL-in-PK semantics (#189 round-2 finding 3)
    qualifier       text NOT NULL DEFAULT '',
    -- exactly ONE value, mirroring event_observation (round-2 finding 1:
    -- bool is first-class, never squeezed into number/text)
    value_number    double precision,
    value_text      text,
    value_bool      boolean,
    CHECK (num_nonnulls(value_number, value_text, value_bool) = 1),
    unit            text,
    -- one vocabulary with event_observation: occurred_at, no observed_at alias
    -- (round-2 finding 3). Both NOT NULL: a facet row IS a projected
    -- measurement; absence of measurement is row absence, never a null time.
    occurred_at     text NOT NULL,
    fetched_at      text NOT NULL,
    -- full source binding back to the carrying Observation
    source_ref      text NOT NULL,
    source_event_id text NOT NULL,
    PRIMARY KEY (node_ref, predicate, qualifier)
);
