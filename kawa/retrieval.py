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
- Scope boundary (#146 / ADV-02, scope-resolution v0.1): every query class filters on the
  viewer's authorized scopes IN SQL, before budgets and aggregation. `viewer_scopes` is a
  trusted execution parameter — never part of Intent, so caller text can never widen
  authorization (§5: providing a scope does not grant access). Omission is NOT all scopes:
  the default grants `fleet` only; envelope-v1 events (`scope_ref IS NULL`, the stated
  rolling-transition carve-out of sql/0010) remain visible. Out-of-scope matches in
  STRUCTURAL classes (ref-driven off a visible anchor) are withheld with an aggregate
  COUNT per class (`Bundle.scope_withheld`) — stated, never silent, never identifying
  (no refs, no relations). The lexical class reports NO count: its terms are
  caller-chosen free text, and an exact count would be a blind existence oracle over
  restricted content. Disclosure fails closed; the honesty rule above keeps holding.
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
from typing import Protocol, Sequence

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

# #146: default viewer grant — fleet plus the envelope-v1 NULL carve-out, nothing more.
_DEFAULT_SCOPES: frozenset[str] = frozenset({"fleet"})

# SQL fragment over an `events` alias: in-scope iff unscoped legacy (v1) or granted.
_IN_SCOPE = "({a}.scope_ref IS NULL OR {a}.scope_ref = ANY(%s))"
_OUT_SCOPE = "({a}.scope_ref IS NOT NULL AND NOT {a}.scope_ref = ANY(%s))"


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
    vector_frontier: list[str] = field(default_factory=list)            # index lag / model, stated (§10.3)
    viewer_scopes: tuple[str, ...] = ()                                 # #146: the scope answered under
    scope_withheld: dict[str, int] = field(default_factory=dict)        # class_id -> count (no refs)


# ---- phase 1: binding (state-dependent, honestly — bound refs become provenance) ----

def resolve_bindings(conn: psycopg.Connection, intent: Intent,
                     viewer_scopes: frozenset[str] = _DEFAULT_SCOPES) -> BoundIntent:
    """#146: binding IS the anchor's authorization gate — an out-of-scope ref binds to
    nothing (scope-resolution v0.1 §4: zero candidates -> not_found; existence is not
    disclosed). Projections (plan/work) bind iff at least one defining event is in scope."""
    anchor_kind = anchor_ref = None
    scopes = list(viewer_scopes)
    ev_in = _IN_SCOPE.format(a="e")
    with conn.cursor() as cur:
        if intent.about:
            cur.execute("SELECT 1 FROM event_claim ec JOIN events e ON e.event_id=ec.event_id "
                        f"WHERE ec.event_id=%s AND {ev_in}", (intent.about, scopes))
            if cur.fetchone():
                anchor_kind, anchor_ref = "claim_event", intent.about
            else:
                cur.execute(f"SELECT 1 FROM events e WHERE e.event_id=%s AND {ev_in}",
                            (intent.about, scopes))
                if cur.fetchone():
                    anchor_kind, anchor_ref = "event", intent.about
                else:
                    cur.execute(
                        "SELECT 1 FROM current_plans cp WHERE cp.plan_ref=%s AND EXISTS "
                        "  (SELECT 1 FROM event_plan p JOIN events e ON e.event_id=p.event_id "
                        f"   WHERE p.plan_ref=cp.plan_ref AND {ev_in})", (intent.about, scopes))
                    if cur.fetchone():
                        anchor_kind, anchor_ref = "plan", intent.about
                    else:
                        cur.execute(
                            "SELECT 1 FROM current_work cw WHERE cw.work_ref=%s AND EXISTS "
                            "  (SELECT 1 FROM event_work w JOIN events e ON e.event_id=w.event_id "
                            f"   WHERE w.work_ref=cw.work_ref AND {ev_in})", (intent.about, scopes))
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
            "neighborhood": "traversal", "lexical": "fts", "vector": "vector"}


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
    if purposes:
        # step 11B (#122, measured GO on evidence+neighborhood): every plannable
        # intent also gets a vector class — LAST, so the structural and lexical
        # classes keep their pre-11B budget precedence (Lock 3 remainder order):
        # vector adds reach, it never outranks structure. Anchored intents need
        # NO live model (the query vector is the anchor's STORED embedding);
        # textual intents embed live and are stated as skipped without a model.
        purposes.append(("vector", None))
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

def retrieve(conn: psycopg.Connection, intent: Intent,
             embedder: "VectorEmbedder | None" = None, *,
             viewer_scopes: frozenset[str] = _DEFAULT_SCOPES) -> Bundle:
    plan = compile_plan(resolve_bindings(conn, intent, viewer_scopes))
    bundle = Bundle(plan=plan, viewer_scopes=tuple(sorted(viewer_scopes)))
    structural_total = 0
    for qc in plan.query_classes:
        if qc.fts_reason == "structured_underflow":
            # execute-time fallback decision: fire only if structure came up short
            if structural_total >= (plan.bound.intent.fallback_policy or 0):
                bundle.skipped_classes.append(
                    f"{qc.class_id} (threshold met: {structural_total} structural records)")
                continue
        if qc.purpose == "vector":
            records = _exec_vector(conn, plan, qc, bundle, embedder, viewer_scopes)
        else:
            records = _EXECUTORS[qc.purpose](conn, plan, qc, bundle, viewer_scopes)
        if qc.backend not in ("fts", "vector"):
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


def _withhold(bundle: Bundle, qc: QueryClass, n: int) -> None:
    if n > 0:
        bundle.scope_withheld[qc.class_id] = bundle.scope_withheld.get(qc.class_id, 0) + n


def _exec_anchor(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                 bundle: Bundle, viewer_scopes: frozenset[str]) -> list[Record]:
    # anchor authorization happened at binding (resolve_bindings) — an out-of-scope
    # anchor never reaches an executor. #146 F1: content fields of projection anchors
    # come from the latest IN-SCOPE defining event, never from the unscoped projection
    # tables — a mixed-scope plan's restricted revision must not leak through
    # current_plans.objective. Protocol-state enums (lifecycle/execution) stay from the
    # projection: closed vocabularies derived by reducers, no payload content.
    b = plan.bound
    scopes = list(viewer_scopes)
    ev_in = _IN_SCOPE.format(a="e")
    with conn.cursor() as cur:
        if b.anchor_kind in ("claim_event", "event"):
            kind, summary = _summary_of(cur, b.anchor_ref)
            return [Record(ref=b.anchor_ref, kind=kind, summary=summary, backend=qc.backend,
                           class_id=qc.class_id, path=b.anchor_ref,
                           standing=_standing_of(cur, b.anchor_ref))]
        if b.anchor_kind == "plan":
            cur.execute("SELECT lifecycle FROM current_plans WHERE plan_ref=%s", (b.anchor_ref,))
            lc = cur.fetchone()[0]
            cur.execute(
                "SELECT p.objective FROM event_plan p JOIN events e ON e.event_id=p.event_id "
                f"WHERE p.plan_ref=%s AND p.objective IS NOT NULL AND {ev_in} "
                "ORDER BY e.origin_node, e.origin_seq DESC LIMIT 1", (b.anchor_ref, scopes))
            row = cur.fetchone()
            o = row[0] if row else ""                    # all objective revisions restricted
            return [Record(ref=b.anchor_ref, kind="plan", summary=f"[{lc}] {o}",
                           backend=qc.backend, class_id=qc.class_id, path=b.anchor_ref)]
        if b.anchor_kind == "work":
            cur.execute("SELECT execution FROM current_work WHERE work_ref=%s", (b.anchor_ref,))
            ex = cur.fetchone()[0]
            cur.execute(
                "SELECT w.plan_ref, w.work_kind FROM event_work w "
                "JOIN events e ON e.event_id=w.event_id "
                f"WHERE w.work_ref=%s AND {ev_in} "
                "ORDER BY e.origin_node, e.origin_seq DESC LIMIT 1", (b.anchor_ref, scopes))
            row = cur.fetchone()
            pr, wk = row if row else ("", "")
            return [Record(ref=b.anchor_ref, kind="work", summary=f"[{ex}] {wk} of {pr}",
                           backend=qc.backend, class_id=qc.class_id, path=b.anchor_ref)]
    return []


def _exec_standing(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                   bundle: Bundle, viewer_scopes: frozenset[str]) -> list[Record]:
    anchor = plan.bound.anchor_ref
    with conn.cursor() as cur:
        standing = _standing_of(cur, anchor)
        if standing is None:
            return []
        return [Record(ref=anchor, kind="claim_standing", summary=standing, backend=qc.backend,
                       class_id=qc.class_id, path=anchor, standing=standing)]


def _exec_evidence(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                   bundle: Bundle, viewer_scopes: frozenset[str]) -> list[Record]:
    """Resolved supports/contradicts edges touching the anchor — fair-share budget between
    the two relation types (Lock 2): floor(budget/2) each, leftovers interleaved."""
    anchor = plan.bound.anchor_ref
    scopes = list(viewer_scopes)
    out: list[Record] = []
    with conn.cursor() as cur:
        per = max(qc.budget // 2, 1)
        # #146 F4: an edge is disclosed only when BOTH the endpoint event AND the
        # asserting link event are in scope — a restricted-scope assertion between
        # public events is itself restricted content (who related what to what).
        _edge_in = f"{_IN_SCOPE.format(a='e')} AND {_IN_SCOPE.format(a='le')}"
        for relation in ("supports", "contradicts"):     # fixed order, EQUAL budget — no bias
            cur.execute(
                "SELECT l.source_ref FROM event_links l "
                "JOIN events e ON e.event_id=l.source_ref "
                "JOIN events le ON le.event_id=l.asserted_by_event_id "
                f"WHERE l.resolved AND l.relation=%s AND l.target_ref=%s AND {_edge_in} "
                "ORDER BY l.source_ref LIMIT %s", (relation, anchor, scopes, scopes, per))
            for (src,) in cur.fetchall():
                kind, summary = _summary_of(cur, src)
                out.append(Record(ref=src, kind=kind, summary=summary, backend=qc.backend,
                                  class_id=qc.class_id, path=f"{anchor} <-{relation}- {src}",
                                  standing=_standing_of(cur, src)))
            # withheld: aggregate count only — no refs, no per-relation split (a split
            # would itself disclose which side of the evidence is being withheld)
            cur.execute(
                "SELECT count(*) FROM event_links l "
                "JOIN events e ON e.event_id=l.source_ref "
                "JOIN events le ON le.event_id=l.asserted_by_event_id "
                f"WHERE l.resolved AND l.relation=%s AND l.target_ref=%s AND NOT ({_edge_in})",
                (relation, anchor, scopes, scopes))
            _withhold(bundle, qc, cur.fetchone()[0])
    return out


def _exec_neighborhood(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                       bundle: Bundle, viewer_scopes: frozenset[str]) -> list[Record]:
    """Deterministic BFS over resolved links, both directions. Expansion order: relation
    GROUP priority (Lock 2 fair-share inside group 1 via alternation), then target hlc,
    then ref. Frontier entries on every stop; unresolved links surface separately.
    #146: edges only expand to in-scope endpoints (SQL-level join); out-of-scope edges
    are counted into scope_withheld without appearing in any frontier (a frontier entry
    would disclose the restricted ref and relation)."""
    b = plan.bound
    root = b.anchor_ref
    scopes = list(viewer_scopes)
    depth_cap, row_cap = b.intent.relation_depth, qc.budget
    out: list[Record] = []
    visited: set[str] = {root}
    frontier_q: list[tuple[str, int, str]] = [(root, 0, root)]   # (node, depth, path)

    with conn.cursor() as cur:
        # unresolved links touching the root's component are a SEPARATE fact (step-2
        # frontier). The link event itself must be in scope to be disclosed (its
        # target is a ghost — not held — so the endpoint carries no local scope).
        cur.execute("SELECT l.source_ref, l.relation, l.target_ref FROM event_links l "
                    "JOIN events le ON le.event_id=l.asserted_by_event_id "
                    f"WHERE NOT l.resolved AND (l.source_ref=%s OR l.target_ref=%s) "
                    f"AND {_IN_SCOPE.format(a='le')}", (root, root, scopes))
        for s, r, t in cur.fetchall():
            bundle.unresolved_frontier.append(FrontierEntry(source_node=s, relation=r,
                                                            next_ref=t, reason="unresolved"))
        # #146 F4: same edge rule as evidence — endpoint AND asserting link event
        # must both be in scope for the edge to be expanded or even counted visible.
        _edge_in = f"{_IN_SCOPE.format(a='e')} AND {_IN_SCOPE.format(a='le')}"
        while frontier_q:
            node, depth, path = frontier_q.pop(0)
            cur.execute(
                "SELECT l.relation, l.target_ref AS other, 'out' AS dir FROM event_links l "
                "JOIN events e ON e.event_id=l.target_ref "
                "JOIN events le ON le.event_id=l.asserted_by_event_id "
                f"WHERE l.resolved AND l.source_ref=%s AND {_edge_in} "
                "UNION ALL "
                "SELECT l.relation, l.source_ref AS other, 'in' AS dir FROM event_links l "
                "JOIN events e ON e.event_id=l.source_ref "
                "JOIN events le ON le.event_id=l.asserted_by_event_id "
                f"WHERE l.resolved AND l.target_ref=%s AND {_edge_in}",
                (node, scopes, scopes, node, scopes, scopes))
            edges = cur.fetchall()
            cur.execute(
                "SELECT count(*) FROM ("
                "  SELECT l.target_ref AS other FROM event_links l "
                "  JOIN events e ON e.event_id=l.target_ref "
                "  JOIN events le ON le.event_id=l.asserted_by_event_id "
                f"  WHERE l.resolved AND l.source_ref=%s AND NOT ({_edge_in}) "
                "  UNION ALL "
                "  SELECT l.source_ref FROM event_links l "
                "  JOIN events e ON e.event_id=l.source_ref "
                "  JOIN events le ON le.event_id=l.asserted_by_event_id "
                f"  WHERE l.resolved AND l.target_ref=%s AND NOT ({_edge_in})"
                ") withheld", (node, scopes, scopes, node, scopes, scopes))
            _withhold(bundle, qc, cur.fetchone()[0])
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
                  bundle: Bundle, viewer_scopes: frozenset[str]) -> list[Record]:
    terms = plan.bound.unbound_text or plan.bound.intent.text_terms or ""
    scopes = list(viewer_scopes)
    out: list[Record] = []
    # #146 F2: the lexical class reports NO withheld count. The search terms are
    # caller-chosen free text, so an exact out-of-scope match count would be a blind
    # existence oracle over restricted propositions (dictionary/binary-search
    # enumeration). Structural classes keep their counts — those queries are
    # ref-driven off an already-visible anchor, not content-driven.
    with conn.cursor() as cur:
        cur.execute("SELECT websearch_to_tsquery('english', %s)::text", (terms,))
        bundle.fts_queries.append(f"{qc.fts_reason}: {cur.fetchone()[0]}")
        _match = "to_tsvector('english', ec.proposition) @@ websearch_to_tsquery('english', %s)"
        cur.execute(
            "SELECT ec.event_id, ec.proposition FROM event_claim ec "
            "JOIN events e ON e.event_id=ec.event_id "
            f"WHERE {_match} AND {_IN_SCOPE.format(a='e')} "
            "ORDER BY ec.event_id LIMIT %s", (terms, scopes, qc.budget))
        for eid, prop in cur.fetchall():
            out.append(Record(ref=eid, kind="claim.recorded", summary=prop, backend=qc.backend,
                              class_id=qc.class_id, path=f"fts:{terms!r}",
                              standing=_standing_of(cur, eid)))
        remaining = qc.budget - len(out)
        if remaining > 0:
            # #146 F1: match against the latest IN-SCOPE objective revision, never the
            # unscoped projection — current_plans.objective may hold a restricted
            # revision of a plan that is visible through an earlier in-scope event.
            _pvis = ("SELECT DISTINCT ON (p.plan_ref) p.plan_ref, p.objective "
                     "FROM event_plan p "
                     "JOIN current_plans cp ON cp.plan_ref = p.plan_ref "
                     "JOIN events e ON e.event_id = p.event_id "
                     f"WHERE p.objective IS NOT NULL AND {_IN_SCOPE.format(a='e')} "
                     "ORDER BY p.plan_ref, e.origin_node, e.origin_seq DESC")
            cur.execute(
                f"SELECT v.plan_ref, v.objective FROM ({_pvis}) v "
                "WHERE to_tsvector('english', v.objective) @@ websearch_to_tsquery('english', %s) "
                "ORDER BY v.plan_ref LIMIT %s", (scopes, terms, remaining))
            for pr, o in cur.fetchall():
                out.append(Record(ref=pr, kind="plan", summary=o, backend=qc.backend,
                                  class_id=qc.class_id, path=f"fts:{terms!r}"))
    return out


class VectorEmbedder(Protocol):
    """Structural twin of kawa.embeddings.Embedder — retrieval must not import
    the embedding substrate (the anchored path is pure SQL; only textual
    intents ever need a live model)."""

    @property
    def model_identity(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _indexed_model(cur: psycopg.Cursor) -> tuple[str, int] | None:
    """The model the index answers under: best-covered, deterministic tiebreak.
    State-dependent like resolve_bindings — and reported in the frontier."""
    cur.execute("SELECT model_identity, count(*) FROM content_embedding "
                "GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1")
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def _anchor_content_event(cur: psycopg.Cursor, bound: BoundIntent,
                          scopes: list[str]) -> str | None:
    """The event whose canonical text stands for the anchor (query-vector source).
    claim/event anchors are themselves; a plan is its latest objective-bearing
    event; a work is its work.derived event. #146: for projection anchors, only
    in-scope defining events may stand for the anchor — a mixed-scope plan must
    not derive its query vector from a restricted revision."""
    if bound.anchor_kind in ("claim_event", "event"):
        return bound.anchor_ref
    if bound.anchor_kind == "plan":
        cur.execute(
            "SELECT p.event_id FROM event_plan p JOIN events e ON e.event_id = p.event_id "
            f"WHERE p.plan_ref = %s AND p.objective IS NOT NULL AND {_IN_SCOPE.format(a='e')} "
            "ORDER BY e.origin_node, e.origin_seq DESC LIMIT 1", (bound.anchor_ref, scopes))
        row = cur.fetchone()
        return row[0] if row else None
    if bound.anchor_kind == "work":
        cur.execute(
            "SELECT w.event_id FROM event_work w JOIN events e ON e.event_id = w.event_id "
            f"WHERE w.work_ref = %s AND {_IN_SCOPE.format(a='e')} "
            "ORDER BY e.origin_node, e.origin_seq DESC LIMIT 1",
            (bound.anchor_ref, scopes))
        row = cur.fetchone()
        return row[0] if row else None
    return None


def _domain_ref(cur: psycopg.Cursor, event_id: str, kind: str) -> str:
    """Map an event to the ref space retrieve() answers in (§10.3 / the recall
    corpus label space): plan events -> plan_ref, work.derived -> work_ref,
    everything else -> the event id. Matches the lexical class precedent."""
    if kind in ("plan.created", "plan.lifecycle_changed"):
        cur.execute("SELECT plan_ref FROM event_plan WHERE event_id=%s", (event_id,))
        row = cur.fetchone()
        return row[0] if row else event_id
    if kind == "work.derived":
        cur.execute("SELECT work_ref FROM event_work WHERE event_id=%s", (event_id,))
        row = cur.fetchone()
        return row[0] if row else event_id
    return event_id


def _exec_vector(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                 bundle: Bundle, embedder: VectorEmbedder | None,
                 viewer_scopes: frozenset[str] = _DEFAULT_SCOPES) -> list[Record]:
    """Step 11B (#122): semantic nearest-neighbors over the §12.2 embedding
    materialization. Anchored intents use the anchor's STORED embedding (pure
    SQL — no live model); textual intents embed the unbound text live. Every
    non-firing condition is STATED in skipped_classes; index lag and the
    answering model are stated in vector_frontier. Similarity is presentation
    order — non-epistemic (§10.4): this executor reads, never writes."""
    b = plan.bound
    scopes = list(viewer_scopes)
    with conn.cursor() as cur:
        indexed = _indexed_model(cur)
        if indexed is None:
            bundle.skipped_classes.append(f"{qc.class_id} (vector index empty)")
            return []
        model, _n = indexed
        # coverage stated over the VIEWER's scope — an aggregate over restricted scopes
        # would leak their event volume (#146)
        cur.execute("SELECT count(*) FROM events ev WHERE ev.materialized "
                    f"AND {_IN_SCOPE.format(a='ev')}", (scopes,))
        materialized = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM event_content c "
            "JOIN events ev ON ev.event_id = c.event_id "
            f"WHERE {_IN_SCOPE.format(a='ev')} AND EXISTS "
            "  (SELECT 1 FROM content_embedding e WHERE e.content_digest = c.content_digest "
            "   AND e.model_identity = %s)", (scopes, model))
        covered = cur.fetchone()[0]
        bundle.vector_frontier.append(
            f"model={model} coverage={covered}/{materialized} materialized events")

        anchor_event: str | None = None
        if b.anchor_ref is not None:
            anchor_event = _anchor_content_event(cur, b, scopes)
            qvec: str | None = None
            if anchor_event is not None:
                cur.execute(
                    "SELECT e.embedding::text FROM event_content c "
                    "JOIN content_embedding e ON e.content_digest = c.content_digest "
                    "JOIN events ev ON ev.event_id = c.event_id AND ev.materialized "
                    "WHERE c.event_id = %s AND e.model_identity = %s",
                    (anchor_event, model))
                row = cur.fetchone()
                qvec = row[0] if row else None
            if qvec is None:
                bundle.skipped_classes.append(
                    f"{qc.class_id} (anchor has no embedding under {model})")
                return []
        elif b.unbound_text:
            if embedder is None:
                bundle.skipped_classes.append(
                    f"{qc.class_id} (textual vector needs a live embedder)")
                return []
            if embedder.model_identity != model:
                bundle.skipped_classes.append(
                    f"{qc.class_id} (live embedder {embedder.model_identity} "
                    f"!= indexed model {model})")
                return []
            qvec = "[" + ",".join(f"{v:.8f}" for v in embedder.embed([b.unbound_text])[0]) + "]"
        else:
            return []

        # walk nearest-first in deterministic chunks, dedup to domain refs:
        # several events can share a plan_ref (objective revisions) — keep the
        # nearest. Chunked keyset-free scan (PR #128 review): a ref with many
        # revisions can never starve the budget the way a fixed over-fetch
        # window could. ev.materialized is re-checked here as defence-in-depth
        # for BC-4 — extraction already refuses stubs, the query refuses twice.
        out: list[Record] = []
        seen: set[str] = {b.anchor_ref} if b.anchor_ref else set()
        offset = 0
        chunk = max(qc.budget * 3, 16)
        capped = False
        while len(out) < qc.budget and not capped:
            cur.execute(
                "SELECT c.event_id, ev.kind, 1 - (e.embedding <=> %s::vector) AS sim "
                "FROM event_content c "
                "JOIN content_embedding e ON e.content_digest = c.content_digest "
                "JOIN events ev ON ev.event_id = c.event_id "
                "WHERE e.model_identity = %s AND c.event_id <> %s AND ev.materialized "
                f"AND {_IN_SCOPE.format(a='ev')} "
                "ORDER BY e.embedding <=> %s::vector, c.event_id LIMIT %s OFFSET %s",
                (qvec, model, anchor_event or "", scopes, qvec, chunk, offset))
            rows = cur.fetchall()
            if not rows:
                break
            offset += len(rows)
            for event_id, kind, sim in rows:
                ref = _domain_ref(cur, event_id, kind)
                if ref in seen:
                    continue
                if len(out) >= qc.budget:
                    bundle.traversal_frontier.append(
                        FrontierEntry(source_node=anchor_event or "text", relation="similar",
                                      next_ref=ref, reason="row_cap"))
                    capped = True
                    break
                seen.add(ref)
                rkind, summary = _summary_of(cur, event_id)
                out.append(Record(
                    ref=ref, kind=rkind, summary=summary, backend=qc.backend,
                    class_id=qc.class_id, path=f"vec:{model} sim={sim:.3f}",
                    standing=_standing_of(cur, event_id)))
    return out


_EXECUTORS = {
    "anchor_lookup": _exec_anchor,
    "standing": _exec_standing,
    "evidence": _exec_evidence,
    "neighborhood": _exec_neighborhood,
    "lexical": _exec_lexical,
}
