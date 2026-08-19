"""current_relations read layer — bounded, bidirectional Plan-lineage traversal (#141 rev 3).

> Direct edges are evidenced; traversal composes; standing is joined.

The projection (sql/0017, a VIEW — minimality gate: an eager table needs the measured
depth-2 p95 > 10ms `relations_view_benchmark` Observation first) stores immutable evidenced
assertions; THIS layer joins standing at read time and walks edges both directions under
pinned caps. Latest-assertion-per-5-tuple semantics: the append-only log retains every
prior assertion (event_links / typed event tables are the audit surface); the projection
row's basis_event is the assertion in force.

No public CLI here (rev 3 §7): the Console renderer and #142's orientation surface are the
consumers; naming of any public operation is decided there against kawa.search.
"""
from __future__ import annotations

import psycopg

from kawa.domain.ids import hlc_order_sql

# frozen Event→Relation registry (rev 3 §2/§6) — exactness-tested; storage direction is
# the ASSERTING event's own direction; traversal is bidirectional so no query is
# privileged by row orientation.
RELATION_REGISTRY = {
    "derives":    ("work.derived",             "plan",   "work"),
    "depends_on": ("work.dependency_declared", "work",   "work"),
    "evidences":  ("result.recorded",          "result", "work"),
    "based_on":   ("link.asserted",            "*",      "*"),
}
SUBJECT_KINDS = ("plan", "work", "result", "observation", "claim", "unknown")
STANDING = ("current", "superseded", "retired", "unknown")

# traversal caps (rev 3 §4) — typed truncation, never silent
DEFAULT_DEPTH = 1
MAX_DEPTH = 3
MAX_FAN_OUT = 20
MAX_TOTAL = 100

# deterministic neighbor ordering (rev 3 §7): canonical causal order + event_id tie-break —
# the retained MAX_FAN_OUT edges are identical across nodes and rebuilds
# `current_relations` has no single unique column, and the query below matches on
# `target_id`, so tie-breaking on it would compare a value every matched row shares
# (review round 2). The row identity is the whole relation tuple.
_RELATION_KEY = ("source_kind", "source_id", "relation_kind", "target_kind", "target_id")
_EDGE_ORDER = f"ORDER BY {hlc_order_sql(hlc='effective_hlc', tiebreak='basis_event', unique=_RELATION_KEY)}"

_KNOWN_PLAN_LIFECYCLES = ("draft", "reviewing", "ready", "running", "blocked", "ended")
_KNOWN_WORK_EXECUTIONS = ("ready", "blocked", "finished", "retired", "retryable",
                          "result_recorded", "execution_unknown")


def standing_of(cur, kind: str, subject_id: str) -> str:
    """Joined at read (rev 3 §2) — closed vocabulary, loud unknowns: a missing projection
    row or an out-of-vocabulary lifecycle/execution value is 'unknown', never guessed."""
    if kind == "plan":
        cur.execute("SELECT lifecycle, end_reason FROM current_plans WHERE plan_ref=%s",
                    (subject_id,))
        row = cur.fetchone()
        if row is None or row[0] not in _KNOWN_PLAN_LIFECYCLES:
            return "unknown"
        if row[0] != "ended":
            return "current"
        return "superseded" if row[1] == "superseded" else "retired"
    if kind == "work":
        cur.execute("SELECT execution FROM current_work WHERE work_ref=%s", (subject_id,))
        row = cur.fetchone()
        if row is None or row[0] not in _KNOWN_WORK_EXECUTIONS:
            return "unknown"
        return "retired" if row[0] == "retired" else "current"
    if kind == "claim":
        # claims carry DERIVED epistemic standing (#97 2C) — review 96575b06 F2: a
        # superseded claim must not read as current. The epistemic axis (contested /
        # grounded_supported / contradicted / unevaluated) maps to 'current' — the claim
        # is in force; its epistemic detail is a different axis than lifecycle standing.
        cur.execute("SELECT standing FROM current_claim_standing WHERE claim_event_id=%s",
                    (subject_id,))
        row = cur.fetchone()
        if row is None:
            return "unknown"
        if row[0] == "superseded":
            return "superseded"
        if row[0] in ("contested", "grounded_supported", "contradicted", "unevaluated"):
            return "current"
        return "unknown"                                  # out-of-vocabulary: loud, never coerced
    if kind in ("result", "observation"):
        return "current"                                  # immutable events
    return "unknown"


def traverse(conn: psycopg.Connection, subject_kind: str, subject_id: str, *,
             depth: int = DEFAULT_DEPTH) -> dict:
    """Bounded bidirectional lineage walk. Caps are pinned constants; truncation is DATA:
    the result always carries is_truncated / truncated_at_depth / total_edges_discovered
    (rev 3 §4/§5). 'unknown'-kind nodes are leaves (rev 3 §4): a bad ref cannot poison
    the walk. Read-only."""
    depth = max(1, min(depth, MAX_DEPTH))
    edges: list[dict] = []
    seen_nodes = {(subject_kind, subject_id)}
    seen_edges: set[tuple] = set()
    frontier = [(subject_kind, subject_id)]
    is_truncated = False
    truncated_at_depth: int | None = None

    with conn.cursor() as cur:
        for d in range(1, depth + 1):
            next_frontier: list[tuple[str, str]] = []
            for kind, sid in frontier:
                if len(edges) >= MAX_TOTAL:               # review 96575b06 F1: stop querying
                    is_truncated = True                   # the DB once the cap is hit
                    truncated_at_depth = truncated_at_depth or d
                    break
                if kind == "unknown":
                    continue                              # unknown nodes are leaves
                cur.execute(
                    "SELECT source_kind, source_id, relation_kind, target_kind, target_id, "
                    "       basis_event, effective_hlc FROM current_relations "
                    "WHERE (source_kind=%s AND source_id=%s) "
                    "   OR (target_kind=%s AND target_id=%s) " + _EDGE_ORDER,
                    (kind, sid, kind, sid))
                rows = cur.fetchall()
                if len(rows) > MAX_FAN_OUT:
                    rows, is_truncated = rows[:MAX_FAN_OUT], True
                    truncated_at_depth = truncated_at_depth or d
                for r in rows:
                    key = tuple(r[:5])
                    if key in seen_edges:
                        continue
                    if len(edges) >= MAX_TOTAL:
                        is_truncated = True
                        truncated_at_depth = truncated_at_depth or d
                        break
                    seen_edges.add(key)
                    edges.append({"source_kind": r[0], "source_id": r[1],
                                  "relation_kind": r[2], "target_kind": r[3],
                                  "target_id": r[4], "basis_event": r[5],
                                  "effective_hlc": r[6]})
                    for nk, ni in ((r[0], r[1]), (r[3], r[4])):
                        if (nk, ni) not in seen_nodes:
                            seen_nodes.add((nk, ni))
                            next_frontier.append((nk, ni))
            frontier = next_frontier
            if not frontier or len(edges) >= MAX_TOTAL:
                break
        # standing joined at read for every node touched (never stored)
        nodes = {f"{k}:{i}": standing_of(cur, k, i) for k, i in seen_nodes}

    return {"subject": {"kind": subject_kind, "id": subject_id},
            "depth": depth, "edges": edges, "node_standing": nodes,
            "is_truncated": is_truncated, "truncated_at_depth": truncated_at_depth,
            "total_edges_discovered": len(edges)}
