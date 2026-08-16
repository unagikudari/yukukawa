-- current_relations — rebuildable typed relation projection as a VIEW (#141, plan-relations-projection rev 3).
--
-- Immutable evidenced assertions only: (source_kind, source_id, relation_kind, target_kind,
-- target_id, basis_event, effective_hlc). NO standing column — standing is joined at read
-- from the subject projections (rev 3 §1: a lifecycle change never cascades into edges).
-- Latest assertion per 5-tuple, resolved by the canonical causal order (§3: hlc numeric
-- fields + origin tiebreak; event_id as final deterministic tie-break) — rev 3 §3: the
-- append-only log keeps every prior assertion; this VIEW exposes the one in force.
--
-- A VIEW, not an eager table (rev 3 §5 minimality gate): promotion requires the measured
-- depth-2 p95 > 10ms Observation (relations_view_benchmark), never speculation.
--
-- link.asserted kind resolution (rev 3 §4): refs are event ids; kinds derive from the
-- referenced event's kind via the frozen mapping below; an unresolvable ref or unmapped
-- kind stores 'unknown' — visible, queryable, excluded from traversal expansion in the
-- read layer (an unknown node is a leaf).

CREATE OR REPLACE VIEW current_relations AS
WITH kind_map AS (
    SELECT e.event_id,
           CASE
             WHEN e.kind LIKE 'plan.%'              THEN 'plan'
             WHEN e.kind LIKE 'work.%'              THEN 'work'
             WHEN e.kind = 'result.recorded'        THEN 'result'
             WHEN e.kind = 'observation.recorded'   THEN 'observation'
             WHEN e.kind = 'claim.recorded'         THEN 'claim'
             ELSE 'unknown'
           END AS subject_kind
    FROM events e
),
assertions AS (
    -- plan derives work (the plan is the deriving authority, rev 3 §6)
    SELECT 'plan'::text AS source_kind, ew.plan_ref AS source_id,
           'derives'::text AS relation_kind,
           'work'::text AS target_kind, ew.work_ref AS target_id,
           ew.event_id AS basis_event, e.hlc AS effective_hlc, e.origin_node
    FROM event_work ew JOIN events e ON e.event_id = ew.event_id
    UNION ALL
    -- work depends_on work (dependent -> dependency)
    SELECT 'work', ewd.work_ref, 'depends_on', 'work', ewd.dependency_work_ref,
           ewd.event_id, e.hlc, e.origin_node
    FROM event_work_dependency ewd JOIN events e ON e.event_id = ewd.event_id
    UNION ALL
    -- result evidences work (evidence points AT the claim it supports, rev 3 §6)
    SELECT 'result', er.result_ref, 'evidences', 'work', er.work_ref,
           er.event_id, e.hlc, e.origin_node
    FROM event_result er JOIN events e ON e.event_id = er.event_id
    UNION ALL
    -- src based_on dst, as asserted; ids are event ids, kinds resolved via kind_map
    SELECT COALESCE(km_s.subject_kind, 'unknown'), el.source_ref,
           'based_on',
           COALESCE(km_t.subject_kind, 'unknown'), el.target_ref,
           el.asserted_by_event_id, e.hlc, e.origin_node
    FROM event_links el
    JOIN events e ON e.event_id = el.asserted_by_event_id
    LEFT JOIN kind_map km_s ON km_s.event_id = el.source_ref
    LEFT JOIN kind_map km_t ON km_t.event_id = el.target_ref
    WHERE el.relation = 'based_on'
)
SELECT DISTINCT ON (source_kind, source_id, relation_kind, target_kind, target_id)
       source_kind, source_id, relation_kind, target_kind, target_id,
       basis_event, effective_hlc
FROM assertions
ORDER BY source_kind, source_id, relation_kind, target_kind, target_id,
         split_part(effective_hlc, '.', 1)::bigint DESC,
         split_part(effective_hlc, '.', 2)::bigint DESC,
         origin_node DESC, basis_event DESC;
