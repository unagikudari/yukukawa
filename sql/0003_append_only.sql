-- Kawa Phase 0 — append-only guard (emit-enforcement).
-- The frozen emit-enforcement contract guards UPDATE/DELETE only: the Event log and its typed
-- payloads are immutable once appended. Projections (current_*) stay mutable/disposable.

CREATE OR REPLACE FUNCTION kawa_forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is forbidden (emit-enforcement)', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'events','event_links','event_plan','event_work','event_work_dependency','event_result'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', 'append_only_' || t, t);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION kawa_forbid_mutation()',
            'append_only_' || t, t
        );
    END LOOP;
END $$;
