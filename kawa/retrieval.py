"""SQL-first unified retrieval (#100 rev 2 + round-2 contract locks; v0.5 §10 / #86).

> Retrieval must explain why these records, from these backends, under these limits —
> and what was intentionally not retrieved.

One API surface, many queries: `retrieve(conn, intent)` runs the two-phase model the
round-2 review locked (Lock 1):

    resolve_bindings(intent, catalog) -> BoundIntent      # honest state-dependence:
    compile_plan(bound)               -> RetrievalPlan    #   bound_refs are provenance
    execute(plan)                     -> Bundle

Determinism: same intent + same catalog state -> same RetrievalPlan -> byte-identical
Bundle. The plan object rides in the bundle header, so every record is traceable to the
query class that produced it (§10.3), and what was NOT retrieved is reported as typed
frontiers, never silently dropped.

Epistemic guards:
- FTS fires only for structurally `textual` intents (unbound text terms) or an explicit
  fallback policy — pure-ref intents plan ZERO lexical classes (§10.4; the compiler's
  output is data, so this is testable, not a vibe).
- supports and contradicts share one fair-share budget group (round-2 Lock 2): a biting
  cap can never keep supporting evidence while silently dropping contradiction.
- standing / protocol-state fields are attached verbatim — retrieval never recomputes or
  reinterprets them. Ranking is presentation order and is non-epistemic (§10.4).

This module only reads. The consumers (scripts/ask.py, Console /search) are narrow test
harnesses with no stable external schema — the step-6 MCP surface will wrap `retrieve`
later with no compatibility promise to them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

# Lock 2: priority GROUPS. supports+contradicts share one group with fair-share budgeting;
# a fixed supports-first order would turn a row cap into confirmation bias.
_RELATION_GROUPS: list[tuple[str, ...]] = [
    ("supersedes",),
    ("supports", "contradicts"),           # fair-share within the group
    ("reason_for",),
    ("based_on",),
    ("addresses", "caused_by", "corrects", "resolves", "reviews", "satisfies"),
]
_GROUP_OF = {r: i for i, group in enumerate(_RELATION_GROUPS) for r in group}

_PATH_EDGE_BUDGET = 8      # compact path summaries: beyond this, deterministic truncation


@dataclass(frozen=True)
class Intent:
    about: str | None = None               # ref: event_id / plan_ref / work_ref
    text_terms: str | None = None          # free text; lexical only if it stays unbound
    relation_depth: int = 2
    limit: int = 50
    fallback_policy: int | None = None     # structured_underflow threshold; None = no fallback


@dataclass(frozen=True)
class BoundIntent:
    intent: Intent
    anchor_kind: str | None                # 'claim_event'|'event'|'plan'|'work'|None
    anchor_ref: str | None
    unbound_text: str | None               # text that did not bind -> makes the intent textual


@dataclass(frozen=True)
class QueryClass:
    class_id: str
    purpose: str                           # anchor_lookup|standing|evidence|neighborhood|lexical
    backend: str                           # typed_sql|traversal|fts
    budget: int
    fts_reason: str | None = None          # textual|structured_underflow|explicit (lexical only)


@dataclass(frozen=True)
class RetrievalPlan:
    bound: BoundIntent
    query_classes: tuple[QueryClass, ...]


@dataclass(frozen=True)
class Record:
    ref: str
    kind: str
    summary: str
    backend: str
    class_id: str
    path: str                              # compact: root -> relation classes -> ref
    path_truncated: bool = False
    standing: str | None = None            # attached VERBATIM from current_claim_standing


@dataclass(frozen=True)
class FrontierEntry:
    source_node: str
    relation: str
    next_ref: str | None
    reason: str                            # depth_limit|row_cap|cycle (traversal) — unresolved is separate


@dataclass
class Bundle:
    plan: RetrievalPlan
    sections: dict[str, list[Record]] = field(default_factory=dict)     # class_id -> records
    traversal_frontier: list[FrontierEntry] = field(default_factory=list)
    unresolved_frontier: list[FrontierEntry] = field(default_factory=list)
    empty_classes: list[str] = field(default_factory=list)
    skipped_classes: list[str] = field(default_factory=list)            # deferred lexical, not fired
    fts_queries: list[str] = field(default_factory=list)                # generated tsquery strings


# ---- phase 1: binding (state-dependent, honestly — bound refs become provenance) ----

def resolve_bindings(conn: psycopg.Connection, intent: Intent) -> BoundIntent:
    anchor_kind = anchor_ref = None
    with conn.cursor() as cur:
        if intent.about:
            cur.execute("SELECT 1 FROM event_claim WHERE event_id=%s", (intent.about,))
            if cur.fetchone():
                anchor_kind, anchor_ref = "claim_event", intent.about
            else:
                cur.execute("SELECT 1 FROM events WHERE event_id=%s", (intent.about,))
                if cur.fetchone():
                    anchor_kind, anchor_ref = "event", intent.about
                else:
                    cur.execute("SELECT 1 FROM current_plans WHERE plan_ref=%s", (intent.about,))
                    if cur.fetchone():
                        anchor_kind, anchor_ref = "plan", intent.about
                    else:
                        cur.execute("SELECT 1 FROM current_work WHERE work_ref=%s", (intent.about,))
                        if cur.fetchone():
                            anchor_kind, anchor_ref = "work", intent.about
    unbound = intent.text_terms or None
    return BoundIntent(intent=intent, anchor_kind=anchor_kind, anchor_ref=anchor_ref,
                       unbound_text=unbound)


# ---- phase 2: compile (pure over BoundIntent — Lock 4 dispatch table) ----

_DISPATCH: dict[str, tuple[str, ...]] = {
    # anchor_kind -> fixed purpose expansion (Lock 4). One intent, many queries (§10.2).
    "claim_event": ("anchor_lookup", "standing", "evidence", "neighborhood"),
    "event": ("anchor_lookup", "neighborhood"),
    "plan": ("anchor_lookup", "neighborhood"),
    "work": ("anchor_lookup", "neighborhood"),
}
_BACKEND = {"anchor_lookup": "typed_sql", "standing": "typed_sql", "evidence": "typed_sql",
            "neighborhood": "traversal", "lexical": "fts"}


def compile_plan(bound: BoundIntent) -> RetrievalPlan:
    purposes: list[tuple[str, str | None]] = []          # (purpose, fts_reason)
    if bound.anchor_kind:
        purposes += [(p, None) for p in _DISPATCH[bound.anchor_kind]]
    if bound.unbound_text:
        # Structurally textual — the only path that plans lexical by default. When an
        # explicit fallback_policy is set, the lexical class is DEFERRED: it fires at
        # execute time only if the structural classes return fewer than `threshold`
        # records (fts_reason=structured_underflow); otherwise it is reported as skipped.
        reason = "textual" if bound.intent.fallback_policy is None else "structured_underflow"
        purposes.append(("lexical", reason))
    if not purposes:
        return RetrievalPlan(bound=bound, query_classes=())

    # Lock 3: deterministic budget apportionment — floor split, remainder to earlier
    # classes in dispatch order (anchor first). Global cap == sum of class budgets.
    n = len(purposes)
    base, rem = divmod(max(bound.intent.limit, n), n)
    classes = tuple(
        QueryClass(class_id=f"q{i}-{purpose}", purpose=purpose, backend=_BACKEND[purpose],
                   budget=base + (1 if i < rem else 0), fts_reason=reason)
        for i, (purpose, reason) in enumerate(purposes)
    )
    return RetrievalPlan(bound=bound, query_classes=classes)


# ---- phase 3: execute ----

def retrieve(conn: psycopg.Connection, intent: Intent) -> Bundle:
    plan = compile_plan(resolve_bindings(conn, intent))
    bundle = Bundle(plan=plan)
    structural_total = 0
    for qc in plan.query_classes:
        if qc.fts_reason == "structured_underflow":
            # execute-time fallback decision: fire only if structure came up short
            if structural_total >= (plan.bound.intent.fallback_policy or 0):
                bundle.skipped_classes.append(
                    f"{qc.class_id} (threshold met: {structural_total} structural records)")
                continue
        records = _EXECUTORS[qc.purpose](conn, plan, qc, bundle)
        if qc.backend != "fts":
            structural_total += len(records)
        if records:
            bundle.sections[qc.class_id] = records
        else:
            bundle.empty_classes.append(qc.class_id)     # empty is stated, never silent (§10)
    return bundle


def _summary_of(cur: psycopg.Cursor, event_id: str) -> tuple[str, str]:
    cur.execute("SELECT kind FROM events WHERE event_id=%s", (event_id,))
    row = cur.fetchone()
    if row is None:
        return ("unknown", "")
    kind = row[0]
    if kind == "claim.recorded":
        cur.execute("SELECT proposition FROM event_claim WHERE event_id=%s", (event_id,))
        return (kind, cur.fetchone()[0])
    if kind == "observation.recorded":
        cur.execute("SELECT predicate, coalesce(value_text, value_number::text, value_bool::text, "
                    "value_time) FROM event_observation WHERE event_id=%s", (event_id,))
        p, v = cur.fetchone()
        return (kind, f"{p} = {v}")
    if kind == "link.asserted":
        cur.execute("SELECT source_ref, relation, target_ref FROM event_link WHERE event_id=%s",
                    (event_id,))
        s, r, t = cur.fetchone()
        return (kind, f"{s[:20]}… --{r}--> {t[:20]}…")
    if kind in ("plan.created", "plan.lifecycle_changed"):
        cur.execute("SELECT plan_ref, coalesce(objective, lifecycle) FROM event_plan WHERE event_id=%s",
                    (event_id,))
        pr, o = cur.fetchone()
        return (kind, f"{pr}: {o}")
    if kind == "work.derived":
        cur.execute("SELECT work_ref, plan_ref FROM event_work WHERE event_id=%s", (event_id,))
        w, pr = cur.fetchone()
        return (kind, f"{w} ({pr})")
    if kind == "result.recorded":
        cur.execute("SELECT work_ref, outcome FROM event_result WHERE event_id=%s", (event_id,))
        w, o = cur.fetchone()
        return (kind, f"{w}: {o}")
    return (kind, "")


def _standing_of(cur: psycopg.Cursor, event_id: str) -> str | None:
    cur.execute("SELECT standing FROM current_claim_standing WHERE claim_event_id=%s", (event_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _exec_anchor(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                 bundle: Bundle) -> list[Record]:
    b = plan.bound
    with conn.cursor() as cur:
        if b.anchor_kind in ("claim_event", "event"):
            kind, summary = _summary_of(cur, b.anchor_ref)
            return [Record(ref=b.anchor_ref, kind=kind, summary=summary, backend=qc.backend,
                           class_id=qc.class_id, path=b.anchor_ref,
                           standing=_standing_of(cur, b.anchor_ref))]
        if b.anchor_kind == "plan":
            cur.execute("SELECT objective, lifecycle FROM current_plans WHERE plan_ref=%s",
                        (b.anchor_ref,))
            o, lc = cur.fetchone()
            return [Record(ref=b.anchor_ref, kind="plan", summary=f"[{lc}] {o}",
                           backend=qc.backend, class_id=qc.class_id, path=b.anchor_ref)]
        if b.anchor_kind == "work":
            cur.execute("SELECT plan_ref, work_kind, execution FROM current_work WHERE work_ref=%s",
                        (b.anchor_ref,))
            pr, wk, ex = cur.fetchone()
            return [Record(ref=b.anchor_ref, kind="work", summary=f"[{ex}] {wk} of {pr}",
                           backend=qc.backend, class_id=qc.class_id, path=b.anchor_ref)]
    return []


def _exec_standing(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                   bundle: Bundle) -> list[Record]:
    anchor = plan.bound.anchor_ref
    with conn.cursor() as cur:
        standing = _standing_of(cur, anchor)
        if standing is None:
            return []
        return [Record(ref=anchor, kind="claim_standing", summary=standing, backend=qc.backend,
                       class_id=qc.class_id, path=anchor, standing=standing)]


def _exec_evidence(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                   bundle: Bundle) -> list[Record]:
    """Resolved supports/contradicts edges touching the anchor — fair-share budget between
    the two relation types (Lock 2): floor(budget/2) each, leftovers interleaved."""
    anchor = plan.bound.anchor_ref
    out: list[Record] = []
    with conn.cursor() as cur:
        per = max(qc.budget // 2, 1)
        for relation in ("supports", "contradicts"):     # fixed order, EQUAL budget — no bias
            cur.execute(
                "SELECT source_ref FROM event_links WHERE resolved AND relation=%s AND target_ref=%s "
                "ORDER BY source_ref LIMIT %s", (relation, anchor, per))
            for (src,) in cur.fetchall():
                kind, summary = _summary_of(cur, src)
                out.append(Record(ref=src, kind=kind, summary=summary, backend=qc.backend,
                                  class_id=qc.class_id, path=f"{anchor} <-{relation}- {src}",
                                  standing=_standing_of(cur, src)))
    return out


def _exec_neighborhood(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                       bundle: Bundle) -> list[Record]:
    """Deterministic BFS over resolved links, both directions. Expansion order: relation
    GROUP priority (Lock 2 fair-share inside group 1 via alternation), then target hlc,
    then ref. Frontier entries on every stop; unresolved links surface separately."""
    b = plan.bound
    root = b.anchor_ref
    depth_cap, row_cap = b.intent.relation_depth, qc.budget
    out: list[Record] = []
    visited: set[str] = {root}
    frontier_q: list[tuple[str, int, str]] = [(root, 0, root)]   # (node, depth, path)

    with conn.cursor() as cur:
        # unresolved links touching the root's component are a SEPARATE fact (step-2 frontier)
        cur.execute("SELECT source_ref, relation, target_ref FROM event_links "
                    "WHERE NOT resolved AND (source_ref=%s OR target_ref=%s)", (root, root))
        for s, r, t in cur.fetchall():
            bundle.unresolved_frontier.append(FrontierEntry(source_node=s, relation=r,
                                                            next_ref=t, reason="unresolved"))
        while frontier_q:
            node, depth, path = frontier_q.pop(0)
            cur.execute(
                "SELECT relation, target_ref AS other, 'out' AS dir FROM event_links "
                "WHERE resolved AND source_ref=%s "
                "UNION ALL "
                "SELECT relation, source_ref AS other, 'in' AS dir FROM event_links "
                "WHERE resolved AND target_ref=%s", (node, node))
            edges = cur.fetchall()
            # deterministic expansion order (round-2 lock): group, then interleave the
            # fair-share group by alternation, then target hlc/ref
            def hlc_key(ref: str) -> tuple:
                cur2 = conn.cursor()
                cur2.execute("SELECT hlc, origin_node FROM events WHERE event_id=%s", (ref,))
                row = cur2.fetchone()
                if row is None:
                    return (1, 0, 0, ref)                # uniform 4-tuple: unknown refs sort last, by ref
                phys, logical, _node = row[0].split(".", 2)
                return (0, int(phys), int(logical), row[1])
            edges.sort(key=lambda e: (_GROUP_OF.get(e[0], 99), e[0], hlc_key(e[1]), e[1]))
            # alternate supports/contradicts inside the fair-share group
            fair = [e for e in edges if e[0] in ("supports", "contradicts")]
            sup = [e for e in fair if e[0] == "supports"]
            con = [e for e in fair if e[0] == "contradicts"]
            interleaved: list = []
            for i in range(max(len(sup), len(con))):
                if i < len(sup): interleaved.append(sup[i])
                if i < len(con): interleaved.append(con[i])
            ordered = ([e for e in edges if _GROUP_OF.get(e[0], 99) < 1]
                       + interleaved
                       + [e for e in edges if _GROUP_OF.get(e[0], 99) > 1])
            for relation, other, direction in ordered:
                if other in visited:
                    bundle.traversal_frontier.append(FrontierEntry(node, relation, other, "cycle"))
                    continue
                if depth + 1 > depth_cap:
                    bundle.traversal_frontier.append(FrontierEntry(node, relation, other, "depth_limit"))
                    continue
                if len(out) >= row_cap:
                    bundle.traversal_frontier.append(FrontierEntry(node, relation, other, "row_cap"))
                    continue
                visited.add(other)
                arrow = f"-{relation}->" if direction == "out" else f"<-{relation}-"
                new_path = f"{path} {arrow} {other[:16]}…"
                truncated = new_path.count("-") // 2 > _PATH_EDGE_BUDGET
                kind, summary = _summary_of(cur, other)
                out.append(Record(ref=other, kind=kind, summary=summary, backend=qc.backend,
                                  class_id=qc.class_id,
                                  path=new_path if not truncated else f"{root} …(+{depth + 1} edges) {other[:16]}…",
                                  path_truncated=truncated, standing=_standing_of(cur, other)))
                frontier_q.append((other, depth + 1, new_path))
    return out


def _exec_lexical(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                  bundle: Bundle) -> list[Record]:
    terms = plan.bound.unbound_text or plan.bound.intent.text_terms or ""
    out: list[Record] = []
    with conn.cursor() as cur:
        cur.execute("SELECT websearch_to_tsquery('english', %s)::text", (terms,))
        bundle.fts_queries.append(f"{qc.fts_reason}: {cur.fetchone()[0]}")
        cur.execute(
            "SELECT event_id, proposition FROM event_claim "
            "WHERE to_tsvector('english', proposition) @@ websearch_to_tsquery('english', %s) "
            "ORDER BY event_id LIMIT %s", (terms, qc.budget))
        for eid, prop in cur.fetchall():
            out.append(Record(ref=eid, kind="claim.recorded", summary=prop, backend=qc.backend,
                              class_id=qc.class_id, path=f"fts:{terms!r}",
                              standing=_standing_of(cur, eid)))
        remaining = qc.budget - len(out)
        if remaining > 0:
            cur.execute(
                "SELECT plan_ref, objective FROM current_plans "
                "WHERE to_tsvector('english', objective) @@ websearch_to_tsquery('english', %s) "
                "ORDER BY plan_ref LIMIT %s", (terms, remaining))
            for pr, o in cur.fetchall():
                out.append(Record(ref=pr, kind="plan", summary=o, backend=qc.backend,
                                  class_id=qc.class_id, path=f"fts:{terms!r}"))
    return out


_EXECUTORS = {
    "anchor_lookup": _exec_anchor,
    "standing": _exec_standing,
    "evidence": _exec_evidence,
    "neighborhood": _exec_neighborhood,
    "lexical": _exec_lexical,
}
