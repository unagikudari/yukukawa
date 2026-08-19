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
  REQUIRED execution parameter resolved by the caller from authenticated/authorized
  context — never part of Intent (caller text cannot widen authorization; §5: providing
  a scope does not grant access), and never defaulted: omission is a TypeError at this
  enforcement boundary, not a scope selection (§2: never interpret omission as all — or
  any — scope). The semantic/MCP surface derives scopes from the participant session;
  harnesses pass `FLEET_SCOPES` explicitly. Envelope-v1 events (`scope_ref IS NULL`)
  remain visible as a TRANSITIONAL carve-out (sql/0010 rolling transition) — deliberate
  compatibility, not permanent semantics; it retires when v1 events are archived. Out-of-scope matches in
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

from kawa.domain.ids import PUBLIC_SCOPE, hlc_parts_sort_key, hlc_sort_key

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

# --- bounded candidate expansion (#212 / #214 step 4 / #222) --------------------
#
# Edge visibility is an AND over two independent scopes (#146 F4). Expressing it as a
# FILTER lets rows the viewer cannot see consume the candidate window and push visible
# ones out — which #222's review showed is not a completeness gap but shadow
# censorship: an attacker emits edges toward a public node from an isolated scope, an
# ordinary viewer's window fills with them, the filter drops them all, and the viewer
# sees nothing. No privilege escalation, nothing forged.
#
# So every leaf NAMES both scopes. A pair the viewer does not hold is never walked and
# there is no window for an invisible row to consume. `sql/0027` and `sql/0028` are the
# indexes these seek on; `tests/test_expansion_cost.py` asserts through EXPLAIN that
# both scopes stay index CONDITIONS and neither becomes a Filter.
#
# One leaf per (scope pair, direction) — not per relation. The ranking puts relation
# priority ahead of the HLC, and the first cut satisfied that by partitioning on
# relation, which is correct and does not scale (11 x 2 x |V|^2 leaves: 243 ms of pure
# planning per node at |V|=8). The requirement was never "partition eleven ways" but
# "relation priority precedes hlc in the traversal order", and `relation_rank` carries
# that into the index instead. Containment is unchanged in form: inside a pair leaf the
# ordering IS the global ranking restricted to that pair, so a row excluded from the
# leaf's top-K has K rows of that pair ahead of it globally and cannot be a global
# winner. What matters is that the order inside a partition agrees with the global
# order, never the number of partitions.
#
# DECLARED DEFAULTS, not measurements — step 4's adversarial fixtures are where these
# get validated. Writing a number here and calling it derived is the failure this repo
# already has a rule about.
MAX_VIEWER_SCOPES = 8      # a wider viewer is served partially and told so
LEAF_BUDGET = 128          # leaves emitted per expanded node
CANDIDATE_PER_NODE = 20    # rows examined per pair leaf


def scope_pairs(viewer_scopes: frozenset[str]) -> tuple[tuple[tuple[str, str], ...], int]:
    """The (endpoint_scope, asserter_scope) pairs a viewer may see, and how many the
    budgets dropped.

    Chosen HERE rather than by a database `ORDER BY`: selecting them in SQL would put
    the choice back under collation, which is the two-engines divergence the ordering
    parity mechanism exists to catch, arriving through the pruning path instead of the
    ranking path (#214 round 7).

    Same-scope pairs come first — they are the overwhelming majority of real edges, so
    a budget that binds should spend itself on them before cross-scope combinations.
    Within each group the order is ascending, so two identical requests consult
    identical pairs and produce byte-identical Bundles.

    A viewer wider than `MAX_VIEWER_SCOPES` is served on its first scopes and told how
    many pairs were dropped. It is NOT refused — refusing a legitimately broad viewer
    is also an authorization decision, and a declared partial answer beats no answer
    (#214 rev 6) — and NOT silently truncated: the count compares against what the
    viewer's FULL scope set implies, so dropping whole scopes cannot hide inside a
    report about dropped pairs."""
    # `$public` is the projection's sentinel for `events.scope_ref IS NULL` -- the
    # envelope-v1 carve-out that `_IN_SCOPE` spells as `IS NULL OR = ANY(...)`. The
    # sentinel exists so the predicate stays plain equality and keeps seeking (0026),
    # and equality only works if every viewer is understood to hold it. Leaving it out
    # made unscoped events invisible to everyone rather than visible to everyone --
    # the carve-out inverted, and silently, because "no rows" is what a correctly
    # restrictive filter also returns.
    every = sorted(set(viewer_scopes) | {PUBLIC_SCOPE})
    consulted = every[:MAX_VIEWER_SCOPES]

    def pairs_of(scopes: list[str]) -> list[tuple[str, str]]:
        return [(s, s) for s in scopes] + [(a, b) for a in scopes for b in scopes if a != b]

    kept = pairs_of(consulted)[:LEAF_BUDGET // 2]      # two directions per pair
    return tuple(kept), len(pairs_of(every)) - len(kept)


# The ordering and the authorization both read the endpoint being STEPPED TO, never
# the one already stood on (sql/0030). `{o}` names that endpoint's column prefix.
_LEAF_SQL = (
    "(SELECT relation, {other} AS other, %s AS dir, %s AS leaf, "
    "        {o}_hlc_phys, {o}_hlc_logical, {o}_origin_node FROM event_links "
    " WHERE {key} = %s AND resolved AND {o}_scope_ref = %s AND asserter_scope_ref = %s{rel} "
    " ORDER BY relation_rank, {o}_hlc_phys DESC, {o}_hlc_logical DESC, "
    '          {o}_origin_node COLLATE "C" DESC, {other} COLLATE "C" DESC LIMIT %s)'
)

def _worst(saturating: list[tuple[int, str]]) -> tuple[int, str]:
    """Most saturated leaves, ties broken by ref ASC.

    An earlier cut inverted the ref with `tuple(-ord(c) for c in ref)` and took the
    max. That is wrong for refs where one is a PREFIX of the other: comparing
    `("node")` against `("node_1")` runs out of elements while still tied, and the
    SHORTER tuple compares smaller, so max() returned the longer ref -- the opposite
    of ref ASC (#224 review round 1, finding 2). Two comparisons say it directly and
    have no inversion to get backwards."""
    most = max(n for n, _ in saturating)
    return most, min(ref for n, ref in saturating if n == most)


FAIR_SHARE = ("contradicts", "supports")   # Lock 2: one budget group, ranks 1 and 2


_DIRECTIONS = (("out", "source_ref", "target_ref", "target"),
               ("in", "target_ref", "source_ref", "source"))


def _leaf_plan(pairs: tuple[tuple[str, str], ...]) -> list[tuple]:
    """Every (direction, pair). Directions outermost, pairs innermost — the leaf index
    is a position in THIS list, so `_one_leaf` can decompose it back."""
    return [(d, p) for d in _DIRECTIONS for p in pairs]


def _leaves(cur, node: str, plan: list[tuple], per_node: int,
            relation: str | None = None) -> list[tuple]:
    """One bounded seek per (scope pair, direction), UNION ALL-ed.

    Never `scope_ref IN (...) AND asserter_scope_ref IN (...)` in a single query:
    Postgres answers that with a Bitmap scan and the LIMIT stops short-circuiting,
    which is the measurement that started #222."""
    sql, params, leaf = [], [], 0
    for (direction, key, other, o), (endpoint_scope, asserter_scope) in plan:
            sql.append(_LEAF_SQL.format(key=key, other=other, o=o,
                                        rel=" AND relation = %s" if relation else ""))
            params += [direction, leaf, node, endpoint_scope, asserter_scope]
            if relation:
                params.append(relation)
            params.append(per_node)
            leaf += 1
    if not sql:
        return []
    cur.execute(" UNION ALL ".join(sql), params)
    return cur.fetchall()


def candidate_edges(cur, node: str, pairs: tuple[tuple[str, str], ...],
                    per_node: int) -> tuple[list[tuple], int]:
    """Candidate edges at one node, and how many leaves the cap saturated.

    `relation_rank` orders each leaf by relation priority before hlc, so a leaf that
    stops at `per_node` has dropped only lower-priority relations. That is the right
    thing to drop -- except across the ONE boundary where priority order is not the
    governing rule:

        Lock 2 gives `supports` and `contradicts` a single budget, so a cap must
        never keep supporting evidence while silently dropping contradiction.

    They occupy adjacent ranks, so a saturating leaf CAN return twenty of one and
    none of the other -- the fairness violation, arriving through the mechanism that
    fixed the fan-out. When a leaf saturates and a fair-share relation is missing
    from what came back, that relation gets its own bounded seek. The extra leaves
    are issued only when the cap actually bit, so the common case (a node whose
    visible degree fits) pays for exactly the leaves it needs.

    The saturated-leaf count is what gets reported, NOT a count of discarded edges:
    counting those exactly means scanning the edges the cap exists to avoid reading,
    so an exact figure would be bought at the price of the bound it describes."""
    rows = _leaves(cur, node, _leaf_plan(pairs), per_node)
    counts: dict[int, int] = {}
    present: dict[int, set[str]] = {}
    for row in rows:
        counts[row[3]] = counts.get(row[3], 0) + 1
        present.setdefault(row[3], set()).add(row[0])
    saturated = [leaf for leaf, n in counts.items() if n >= per_node]

    # Fair share is a property of EACH leaf, not of the node. Checking presence across
    # all leaves at once let one scope pair vouch for another: a leaf holding fifty
    # contradictions and fifty supports saturates on contradictions alone and drops
    # every support, and a single support edge under some UNRELATED scope pair made
    # the aggregate check see `supports` and skip the top-up (#224 review round 1,
    # finding 1). The starved leaf is the one that has to be topped up.
    #
    # Grouped by relation, so a node where every leaf starves costs TWO statements
    # rather than two per leaf — 256 sequential round trips at the worst node, which
    # is the fan-out this whole plan removed, re-entering through its own remedy
    # (#224 review round 2, finding 2).
    plan = _leaf_plan(pairs)
    starved: dict[str, list[int]] = {}
    for leaf in sorted(saturated):
        for relation in FAIR_SHARE:
            if relation not in present[leaf]:
                starved.setdefault(relation, []).append(leaf)
    for relation, leaves in sorted(starved.items()):
        rows += _leaves(cur, node, [plan[leaf] for leaf in leaves], per_node,
                        relation=relation)
    return rows, len(saturated)


# #146: the harness grant — fleet only. Exported for callers that legitimately answer
# under fleet visibility (CLI/console/eval harnesses); production surfaces resolve
# scopes from the participant session instead. There is NO implicit default.
FLEET_SCOPES: frozenset[str] = frozenset({"fleet"})

# SQL fragment over an `events` alias: in-scope iff granted, or unscoped envelope-v1
# (TRANSITIONAL carve-out — retires when v1 events are archived).
_IN_SCOPE = "({a}.scope_ref IS NULL OR {a}.scope_ref = ANY(%s))"
_OUT_SCOPE = "({a}.scope_ref IS NOT NULL AND NOT {a}.scope_ref = ANY(%s))"


@dataclass(frozen=True)
class Intent:
    about: str | None = None               # ref: event_id / plan_ref / work_ref
    text_terms: str | None = None          # free text; lexical only if it stays unbound
    relation_depth: int = 2
    # A HARD ceiling on the Bundle: sum(class budgets) <= limit, for every shape and
    # every value including 0 (#214 step 2). It used to be a floor the planner could
    # enlarge -- `limit=0` authorised fourteen records -- so a caller could not bound
    # its own context deterministically.
    #
    # The default is 59 because that is the largest total any shape produced under the
    # old arithmetic (claim_event at the previous default of 50, measured). Raising it
    # is what keeps callers who never set a limit from silently losing budget the day
    # the number started meaning something.
    limit: int = 59
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
    # every ceiling actually enforced, stated before execution rather than inferred
    # from the result. `result_limit` is the hard one: sum(class budgets) <= it.
    result_limit: int = 0
    skipped_at_compile: tuple[SkippedClass, ...] = ()

    @property
    def total_budget(self) -> int:
        return sum(q.budget for q in self.query_classes)


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
class DocSectionRecord:
    """#156 Phase A: a normative doc-section candidate. Satisfies the Record field
    contract (shared fields render everywhere Record does) plus typed provenance —
    no metadata is regex-packed into summary/path. `ref` uses the content-independent
    section anchor (anchor8); heading renames mint a new anchor and stale refs
    surface as a stated `section_moved` orientation note, never silently."""
    ref: str                               # <doc_path>#<anchor8>
    kind: str                              # 'normative_section'
    summary: str                           # bounded excerpt (<=320 chars, deterministic)
    backend: str                           # 'repository'
    class_id: str
    path: str                              # doc_path / heading_path (display)
    path_truncated: bool = False
    standing: str | None = None            # N/A for sections; kept for field-contract parity
    doc_path: str = ""
    heading_path: str = ""
    authority_status: str = "current"      # matrix-derived; read-through flags ride here
    content_digest: str = ""
    commit: str = ""
    basis: str = "domain"                  # domain | lexical (derived-facet basis rule)


# Why a class produced nothing. A closed vocabulary rather than free text: a reader
# that must parse prose to learn whether something was unaffordable or genuinely
# absent will guess, and "no normative material exists" is a very different claim
# from "no normative material was affordable" (#214 step 2).
SKIP_REASONS = (
    "tier_budget_exhausted",   # the ceiling ran out before this tier was reached
    "budget_exhausted",        # planned, but apportionment left it zero rows
    "structured_underflow_met",  # deferred lexical: structure answered, so it never fired
    "no_model",                # vector: no embedder available
)


@dataclass(frozen=True)
class SkippedClass:
    class_id: str
    purpose: str
    reason: str                            # one of SKIP_REASONS
    detail: str = ""                       # human context; never the machine-readable part

    def __post_init__(self) -> None:
        assert self.reason in SKIP_REASONS, self.reason


@dataclass(frozen=True)
class FrontierEntry:
    source_node: str
    relation: str
    next_ref: str | None
    reason: str                            # depth_limit|row_cap|cycle (traversal) — unresolved is separate


@dataclass(frozen=True)
class CandidateTruncation:
    """What expansion did not look at, said out loud.

    A bounded traversal that stays silent about its bound reads as a complete
    answer, which is the failure mode #212 was: the caller cannot tell "this node
    has three neighbours" from "this node has thirty thousand and we read twenty".

    `nodes` and `worst_node` are IN-SCOPE nodes only. Naming a node whose edges the
    viewer may not see would disclose the restricted ref through the honesty
    channel -- the same mistake the traversal frontier avoids by omitting
    out-of-scope endpoints rather than listing them as skipped."""
    nodes: int                    # in-scope nodes where at least one leaf saturated
    worst_node: str               # the one that saturated most leaves (ties: ref asc)
    worst_leaves: int
    per_node: int                 # the cap that bit, so the report is interpretable
    scope_pairs_dropped: int = 0  # viewer wider than MAX_VIEWER_SCOPES (0 = served whole)


@dataclass
class Bundle:
    plan: RetrievalPlan
    sections: dict[str, list[Record]] = field(default_factory=dict)     # class_id -> records
    traversal_frontier: list[FrontierEntry] = field(default_factory=list)
    unresolved_frontier: list[FrontierEntry] = field(default_factory=list)
    empty_classes: list[str] = field(default_factory=list)
    skipped_classes: list[SkippedClass] = field(default_factory=list)   # planned-but-unrun, typed
    fts_queries: list[str] = field(default_factory=list)                # generated tsquery strings
    vector_frontier: list[str] = field(default_factory=list)            # index lag / model, stated (§10.3)
    viewer_scopes: tuple[str, ...] = ()                                 # #146: the scope answered under
    scope_withheld: dict[str, int] = field(default_factory=dict)        # class_id -> count (no refs)
    candidate_truncation: CandidateTruncation | None = None             # #214 step 4, stated not hidden
    orientation: list[str] = field(default_factory=list)                # #156: stated orientation facts
    #   (derived domains / UNMAPPED frontier / index source+commit / dirty docs /
    #    precedent source boundary) — honesty statements, never candidate content


# ---- phase 1: binding (state-dependent, honestly — bound refs become provenance) ----

def resolve_bindings(conn: psycopg.Connection, intent: Intent,
                     viewer_scopes: frozenset[str]) -> BoundIntent:
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
            "neighborhood": "traversal", "lexical": "fts", "vector": "vector",
            "repository_normative": "repository", "precedent": "typed_sql"}

# #156 Phase A: orientation profiles — compile-time query-class mixes selected from
# the anchor's kind (internal RetrievalPlan behavior, not public surface). The
# sideband is ADDITIVE: Intent.limit bounds the base structural pool exactly as
# before; these rows ride on top (total <= limit + sideband; #156 sketch 6).
# Tiers (#214 step 2). A small ceiling must cost REACH, not GROUNDING: the caller who
# sets a limit to be careful is exactly the one who should not lose the normative
# material. Within tier 1 the waterfall decides, so `limit=1` still answers with the
# anchor rather than with a fragment of everything.
_TIER = {"anchor_lookup": 1, "standing": 1, "repository_normative": 1,
         "evidence": 2,
         "neighborhood": 3, "precedent": 3, "vector": 3, "lexical": 3}
_WITHIN_TIER = ("anchor_lookup", "standing", "repository_normative", "evidence",
                "neighborhood", "precedent", "lexical", "vector")

# The smallest budget at which a class can honour its own contract. One row is
# enough for most, but `evidence` carries Lock 2 — a biting cap must never keep
# supporting evidence while silently dropping contradiction — and ONE row cannot
# represent two sides. At budget 1 the executor previously returned two records
# to keep both, which is how the class exceeded its own budget (#211).
#
# So the conflict is resolved where it belongs, at the floor rather than in the
# executor: evidence is planned with at least 2, or not planned at all. A ceiling
# that cannot afford balanced evidence says so (`tier_budget_exhausted`) instead
# of quietly returning one side and letting a reader mistake it for the whole.
_FLOOR = {"evidence": 2}

_PROFILE_OF = {"work": "design_change", "plan": "design_change",
               "event": "design_change", "claim_event": "adversarial_review"}
_SIDEBAND = {"design_change": (4, 2), "adversarial_review": (6, 3)}   # (normative, precedent) rows


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
        # classes keep their pre-11B budget precedence: vector adds reach, it never
        # outranks structure. Anchored intents need NO live model (the query vector is
        # the anchor's STORED embedding); textual intents embed live and are stated as
        # skipped without a model.
        purposes.append(("vector", None))
    # #156 Phase A: the orientation sideband. It used to be appended AFTER the split,
    # OUTSIDE Intent.limit — which is how `limit=0` authorised fourteen records. It is
    # now an ordinary member of the tiers below, inside the ceiling (#214 step 2).
    if bound.anchor_kind:
        norm_rows, prec_rows = _SIDEBAND[_PROFILE_OF[bound.anchor_kind]]
        purposes += [("repository_normative", None), ("precedent", None)]
    else:
        norm_rows = prec_rows = 0
    if not purposes:
        return RetrievalPlan(bound=bound, query_classes=(), result_limit=bound.intent.limit)

    limit = max(0, bound.intent.limit)
    wants = {"repository_normative": norm_rows, "precedent": prec_rows}

    # Tier order IS execution order, and the ceiling is absolute. Reserving floors at
    # COMPILE time rather than short-circuiting at run time is what makes both true at
    # once: a runtime check either truncates tier 1 at the end (so exempting it bought
    # nothing) or returns past the limit (so the limit is not one). Reserved first,
    # nothing runs that cannot be admitted.
    ordered = sorted(purposes, key=lambda pr: (_TIER[pr[0]], _WITHIN_TIER.index(pr[0])))

    budgets: dict[str, int] = {}
    skipped: list[SkippedClass] = []
    remaining = limit

    # 1. one row each, in tier order, for as far as the ceiling reaches. This is where
    #    tiering earns its keep: at limit=1 the answer is the anchor, not a fragment of
    #    everything, and the classes that fall off the end are the expansive ones.
    # Tier boundaries are HARD. Admitting a cheaper lower tier after a higher one
    # failed its floor looked like thrift and was a contradiction: at limit=4 the
    # plan skipped tier-2 evidence, admitted tier-3 neighborhood, and reported
    # "exhausted before tier 2" while tier 3 sat in the sections. Reach outranking
    # grounding is precisely what tiering exists to prevent, so a tier that cannot
    # be floored ends the admission — the surplus tops up what is already planned.
    for tier in sorted({_TIER[p] for p, _ in ordered}):
        in_tier = [pr for pr in ordered if _TIER[pr[0]] == tier]
        floored = {}
        left = remaining
        for purpose, _reason in in_tier:           # waterfall WITHIN the tier
            floor = _FLOOR.get(purpose, 1)
            if left < floor:
                break
            floored[purpose] = floor
            left -= floor
        budgets.update(floored)
        remaining = left
        if len(floored) < len(in_tier):            # this tier is incomplete: stop here
            break

    planned = [pr for pr in ordered if pr[0] in budgets]
    unplanned = [pr for pr in ordered if pr[0] not in budgets]

    # 2. the remainder, in tier order. Sideband classes are CAPPED at what they want;
    #    everything else splits what is left evenly, earlier tiers first.
    for purpose in list(budgets):
        if purpose in wants and remaining > 0:
            top_up = min(wants[purpose] - budgets[purpose], remaining)
            if top_up > 0:
                budgets[purpose] += top_up
                remaining -= top_up
    shares = [pr for pr in planned if pr[0] not in wants]
    if shares and remaining > 0:
        base, rem = divmod(remaining, len(shares))
        for i, (purpose, _reason) in enumerate(shares):
            budgets[purpose] += base + (1 if i < rem else 0)
        remaining = 0

    # 3. what the ceiling never reached is stated, not silently absent
    for purpose, _reason in unplanned:
        skipped.append(SkippedClass(
            _class_id(ordered, purpose), purpose, "tier_budget_exhausted",
            f"limit {limit} was exhausted before tier {_TIER[purpose]}"))

    classes = tuple(
        QueryClass(class_id=_class_id(ordered, purpose), purpose=purpose,
                   backend=_BACKEND[purpose], budget=budgets[purpose], fts_reason=reason)
        for purpose, reason in ordered if purpose in budgets
    )
    total = sum(q.budget for q in classes)
    if total > limit:
        # Not an assert: `python -O` strips those, and a guarantee that evaporates
        # under an optimisation flag is not one. This is the property the whole step
        # exists to establish, so it is checked where it cannot be turned off.
        raise RuntimeError(
            f"apportionment exceeded the ceiling ({total} > {limit}) — refusing to "
            "return a plan that cannot honour Intent.limit")
    return RetrievalPlan(bound=bound, query_classes=classes, result_limit=limit,
                         skipped_at_compile=tuple(skipped))


def _class_id(ordered: list[tuple[str, str | None]], purpose: str) -> str:
    """Stable per-plan identifier: position in the tier-ordered dispatch."""
    return f"q{[p for p, _ in ordered].index(purpose)}-{purpose}"


# ---- phase 3: execute ----

def retrieve(conn: psycopg.Connection, intent: Intent,
             embedder: "VectorEmbedder | None" = None, *,
             viewer_scopes: frozenset[str]) -> Bundle:
    plan = compile_plan(resolve_bindings(conn, intent, viewer_scopes))
    bundle = Bundle(plan=plan, viewer_scopes=tuple(sorted(viewer_scopes)))
    bundle.skipped_classes.extend(plan.skipped_at_compile)   # never planned, and says so
    structural_total = 0
    for qc in plan.query_classes:
        if qc.fts_reason == "structured_underflow":
            # execute-time fallback decision: fire only if structure came up short
            if structural_total >= (plan.bound.intent.fallback_policy or 0):
                bundle.skipped_classes.append(SkippedClass(
                    qc.class_id, qc.purpose, "structured_underflow_met",
                    f"{structural_total} structural records met the threshold"))
                continue
        if qc.purpose == "vector":
            records = _exec_vector(conn, plan, qc, bundle, embedder, viewer_scopes)
        else:
            records = _EXECUTORS[qc.purpose](conn, plan, qc, bundle, viewer_scopes)
        if qc.backend not in ("fts", "vector") and qc.purpose not in (
                "repository_normative", "precedent"):
            # #156: sideband classes never feed the structural-underflow decision —
            # pre-Phase-A fallback behavior is byte-identical.
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
    """Resolved supports/contradicts edges touching the anchor, sharing ONE budget.

    Lock 2 says a biting cap must never keep supporting evidence while silently
    dropping contradiction. It used to do that by giving each relation its own
    `LIMIT per`, with `per = max(budget // 2, 1)` — which meant a class with
    budget 1 returned TWO records, and #211 measured the class exceeding its own
    budget for every odd budget below 4. The fairness was real and the bound was
    not.

    The two are now separable: fetch up to `budget` from EACH relation (so a side
    with few edges cannot be starved by a side with many), then interleave and
    truncate to `budget`. At most `2 * budget` rows are read to decide `budget`
    rows — bounded by the class budget, never by the anchor's degree — and the
    result satisfies `len(out) <= qc.budget` for every budget, including 1.

    Interleaving rather than concatenating is what keeps the truncation fair: the
    cut falls evenly across both sides at any budget. The order is fixed —
    `supports` first — so at an odd budget the extra row is a support. That
    residual is deliberate: the floor of 2 means the smallest odd budget is 3
    (a 2:1 split, converging to even as the budget grows), and alternating by
    anchor would trade a predictable, auditable order for no change in balance.
    """
    anchor = plan.bound.anchor_ref
    scopes = list(viewer_scopes)
    per_relation: dict[str, list[Record]] = {}
    with conn.cursor() as cur:
        # #146 F4: an edge is disclosed only when BOTH the endpoint event AND the
        # asserting link event are in scope — a restricted-scope assertion between
        # public events is itself restricted content (who related what to what).
        _edge_in = f"{_IN_SCOPE.format(a='e')} AND {_IN_SCOPE.format(a='le')}"
        for relation in ("supports", "contradicts"):     # fixed order, EQUAL window — no bias
            cur.execute(
                "SELECT l.source_ref FROM event_links l "
                "JOIN events e ON e.event_id=l.source_ref "
                "JOIN events le ON le.event_id=l.asserted_by_event_id "
                f"WHERE l.resolved AND l.relation=%s AND l.target_ref=%s AND {_edge_in} "
                "ORDER BY l.source_ref LIMIT %s",
                (relation, anchor, scopes, scopes, qc.budget))
            # refs only here: summary/standing are two secondary queries EACH, and up
            # to half of these rows are about to be truncated away. Hydrate after the
            # cut, not before (review round 1).
            per_relation[relation] = [src for (src,) in cur.fetchall()]
            # withheld: aggregate count only — no refs, no per-relation split (a split
            # would itself disclose which side of the evidence is being withheld)
            cur.execute(
                "SELECT count(*) FROM event_links l "
                "JOIN events e ON e.event_id=l.source_ref "
                "JOIN events le ON le.event_id=l.asserted_by_event_id "
                f"WHERE l.resolved AND l.relation=%s AND l.target_ref=%s AND NOT ({_edge_in})",
                (relation, anchor, scopes, scopes))
            _withhold(bundle, qc, cur.fetchone()[0])

    sup, con = per_relation["supports"], per_relation["contradicts"]
    interleaved: list[tuple[str, str]] = []
    for i in range(max(len(sup), len(con))):
        if i < len(sup):
            interleaved.append(("supports", sup[i]))
        if i < len(con):
            interleaved.append(("contradicts", con[i]))

    out: list[Record] = []
    with conn.cursor() as cur:
        for relation, src in interleaved[:qc.budget]:
            kind, summary = _summary_of(cur, src)
            out.append(Record(ref=src, kind=kind, summary=summary, backend=qc.backend,
                              class_id=qc.class_id, path=f"{anchor} <-{relation}- {src}",
                              standing=_standing_of(cur, src)))
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
        # #146 F4: an edge is disclosed only when BOTH the endpoint event AND the
        # asserting link event are in scope. Both scopes now live ON the link row
        # (sql/0026), so that rule is two equality keys in an index rather than two
        # joins with a residual filter — which is what makes the traversal bounded:
        # a filtered join reads invisible rows to discard them, and those reads are
        # the window a low-privilege actor floods to erase a high-privilege viewer's
        # sight of the public graph (#222 round 1).
        pairs, pairs_dropped = scope_pairs(viewer_scopes)
        saturating: list[tuple[int, str]] = []
        while frontier_q:
            node, depth, path = frontier_q.pop(0)
            # The ordering components come back WITH the edge (#214 step 4). They live
            # on the link row now, so ranking needs no second query per edge — see the
            # note on `hlc_key` below for what that replaced.
            edges, saturated = candidate_edges(cur, node, pairs, CANDIDATE_PER_NODE)
            if saturated:
                saturating.append((saturated, node))
            # withheld: everything resolved at this node that the viewer may not see.
            # No joins — the scopes are on the row, so this is a count over the node's
            # own edges rather than over the graph.
            cur.execute(
                "SELECT count(*) FROM event_links WHERE resolved AND (source_ref=%s OR target_ref=%s)"
                " AND NOT ((CASE WHEN source_ref=%s THEN target_scope_ref"
                "                ELSE source_scope_ref END) = ANY(%s)"
                "          AND asserter_scope_ref = ANY(%s))",
                (node, node, node, scopes, scopes))
            # A NULL source scope makes the predicate UNKNOWN and the row uncounted,
            # which is the honest answer: an edge whose far endpoint this node does
            # not hold is not being WITHHELD from the viewer, it is absent from the
            # log. Counting it would report a restricted neighbour that may not exist.
            _withhold(bundle, qc, cur.fetchone()[0])
            # The leaf index did its job during saturation counting and is not part of
            # an edge's identity — two viewer scope pairs can both disclose the same
            # edge. Project it out, then dedup, then sort a TOTAL order.
            #
            # `list(set(...))` plus two stable sorts was not enough: an in-edge and an
            # out-edge between the same two nodes tie on relation, phys, logical,
            # origin_node and `other`, differing only in `dir` — which appeared in
            # neither sort key. Stable sorting preserved their `set` iteration order,
            # and that order varies with the process hash seed, so two replicas holding
            # identical events produced different `Record.path` strings (#224 review
            # round 2, finding 1). A Bundle that differs run to run cannot be cited.
            #
            # The real error was upstream of the sort: `other` was passed as the unique
            # key, and `other` is not unique. The unique key of a traversal candidate is
            # (other, direction).
            edges = sorted({(rel, other, direction, phys, logical, node_id)
                            for rel, other, direction, _leaf, phys, logical, node_id in edges})
            # Expansion order (round-2 lock): relation group, the fair-share group
            # interleaved by alternation, then the causal order.
            #
            # ONE ordering rule (#214 step 1), reached through the components the
            # row already carries rather than re-serialising them into a stamp.
            #
            # This used to hand-roll `(0, phys, logical, node_id, other)` and sort
            # ASCENDING, while the canonical order — and the leaf SQL that now does
            # the truncating — is DESCENDING. Self-consistent while nothing was
            # truncated; incoherent the moment a cap bit, because the leaf kept the
            # NEWEST rows and expansion then walked the OLDEST of them first. The
            # hlc lint could not catch it: its pattern looks for the token `hlc` near
            # a `key=`, and the components are named `phys`/`logical` here (#224
            # review round 1, finding 3). A test pins it instead of a regex.
            #
            # Two stable passes rather than one key, so nothing has to be negated to
            # make it descend — negating a text tie-break is exactly the inversion
            # bug that finding 2 was.
            def edge_key(edge: tuple) -> tuple:
                _relation, other, direction, phys, logical, node_id = edge
                return hlc_parts_sort_key(phys, logical, node_id or "", (other, direction))
            edges.sort(key=edge_key, reverse=True)                       # least significant
            edges.sort(key=lambda e: (_GROUP_OF.get(e[0], 99), e[0]))    # most significant
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
            for relation, other, direction, *_order in ordered:
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

    if saturating or pairs_dropped:
        # tie-break on the ref so two identical requests name the same node — the
        # report is part of the Bundle, and a Bundle that differs run to run cannot
        # be cited. Every node here was expanded, so every node here is in scope.
        worst_leaves, worst_node = _worst(saturating) if saturating else (0, "")
        bundle.candidate_truncation = CandidateTruncation(
            nodes=len(saturating), worst_node=worst_node, worst_leaves=worst_leaves,
            per_node=CANDIDATE_PER_NODE, scope_pairs_dropped=pairs_dropped)
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
                 viewer_scopes: frozenset[str]) -> list[Record]:
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
            bundle.skipped_classes.append(SkippedClass(
                qc.class_id, qc.purpose, "no_model", "vector index empty"))
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
                bundle.skipped_classes.append(SkippedClass(
                    qc.class_id, qc.purpose, "no_model",
                    f"anchor has no embedding under {model}"))
                return []
        elif b.unbound_text:
            if embedder is None:
                bundle.skipped_classes.append(SkippedClass(
                    qc.class_id, qc.purpose, "no_model",
                    "textual vector needs a live embedder"))
                return []
            if embedder.model_identity != model:
                bundle.skipped_classes.append(SkippedClass(
                    qc.class_id, qc.purpose, "no_model",
                    f"live embedder {embedder.model_identity} != indexed model {model}"))
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


# ---- #156 Phase A: principle-aware orientation executors ----

def _registry_domains() -> dict[str, list[str]]:
    import json
    from pathlib import Path
    reg = Path(__file__).resolve().parents[1] / "registry" / "vocabulary.json"
    try:
        return json.loads(reg.read_text()).get("domains", {})
    except OSError:
        return {}


_SECTION_INDEX_CACHE: dict[str, object] = {}      # commit -> SectionIndex (sections immutable per commit)


def _section_index():  # type: ignore[no-untyped-def]
    """Answer-time index acquisition (#156 sketch 4): resolve HEAD, reuse the
    per-commit cached sections, and re-check working-tree dirtiness on every call
    (dirtiness is the only non-commit-determined input). Unavailable git/checkout
    is a stated skip, never an error."""
    from pathlib import Path

    from kawa.repo_sections import build_index
    repo = Path(__file__).resolve().parents[1]
    try:
        idx = build_index(repo)
    except Exception:
        return None
    return idx


def _derive_domains(cur: psycopg.Cursor, b: BoundIntent,
                    scopes: list[str]) -> tuple[list[str], str]:
    """#156 sketch 2 — deterministic per-anchor precedence over TYPED declarations
    (never raw text). Returns (domains, basis_note). Work: own domain token, else
    the owning Plan's token. (subject_ref aggregate typing awaits a subject
    registry — until then it contributes no signal, honestly.) Plan: own token,
    else union of its Works' tokens. Claim/event: tokens of the plan/work events
    in its based_on lineage. Nothing found => UNMAPPED (stated frontier)."""
    ev_in = _IN_SCOPE.format(a="e")

    def plan_domain(plan_ref: str) -> str | None:
        cur.execute(
            "SELECT p.domain FROM event_plan p JOIN events e ON e.event_id=p.event_id "
            f"WHERE p.plan_ref=%s AND p.domain IS NOT NULL AND {ev_in} "
            "ORDER BY e.origin_node, e.origin_seq DESC LIMIT 1", (plan_ref, scopes))
        row = cur.fetchone()
        return row[0] if row else None

    if b.anchor_kind == "work":
        cur.execute(
            "SELECT w.domain, w.plan_ref FROM event_work w JOIN events e ON e.event_id=w.event_id "
            f"WHERE w.work_ref=%s AND {ev_in} ORDER BY e.origin_node, e.origin_seq DESC LIMIT 1",
            (b.anchor_ref, scopes))
        row = cur.fetchone()
        if row:
            own, plan_ref = row
            if own:
                return [own], "work.domain"
            pd = plan_domain(plan_ref)
            if pd:
                return [pd], "plan.domain (lineage)"
        return [], "UNMAPPED"
    if b.anchor_kind == "plan":
        pd = plan_domain(b.anchor_ref)
        if pd:
            return [pd], "plan.domain"
        cur.execute(
            "SELECT DISTINCT w.domain FROM event_work w JOIN events e ON e.event_id=w.event_id "
            f"WHERE w.plan_ref=%s AND w.domain IS NOT NULL AND {ev_in}", (b.anchor_ref, scopes))
        ds = sorted(r[0] for r in cur.fetchall())
        return (ds, "works.domain (lineage)") if ds else ([], "UNMAPPED")
    if b.anchor_kind in ("claim_event", "event"):
        cur.execute(
            "SELECT l.target_ref FROM event_links l WHERE l.resolved AND l.relation='based_on' "
            "AND l.source_ref=%s ORDER BY l.target_ref", (b.anchor_ref,))
        targets = [r[0] for r in cur.fetchall()]
        ds: set[str] = set()
        for t in targets:
            cur.execute(
                "SELECT w.domain FROM event_work w JOIN events e ON e.event_id=w.event_id "
                f"WHERE w.event_id=%s AND w.domain IS NOT NULL AND {ev_in}", (t, scopes))
            ds.update(r[0] for r in cur.fetchall())
            cur.execute(
                "SELECT p.domain FROM event_plan p JOIN events e ON e.event_id=p.event_id "
                f"WHERE p.event_id=%s AND p.domain IS NOT NULL AND {ev_in}", (t, scopes))
            ds.update(r[0] for r in cur.fetchall())
        return (sorted(ds), "based_on lineage") if ds else ([], "UNMAPPED")
    return [], "UNMAPPED"


_STOPWORDS = frozenset({"this", "that", "with", "from", "have", "must", "should", "when",
                        "which", "where", "into", "only", "over", "such", "then", "than"})


def _exec_repository_normative(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                               bundle: Bundle, viewer_scopes: frozenset[str]) -> list[Record]:
    b = plan.bound
    scopes = list(viewer_scopes)
    idx = _section_index()
    if idx is None:
        bundle.skipped_classes.append(SkippedClass(
            qc.class_id, qc.purpose, "no_model", "repository index unavailable"))
        return []
    bundle.orientation.append(f"index source: {idx.source} @ {idx.commit[:12]}")
    for doc in idx.dirty_docs:
        bundle.orientation.append(f"repository_dirty: {doc} (sections withheld)")
    with conn.cursor() as cur:
        domains, basis_note = _derive_domains(cur, b, scopes)
    bundle.orientation.append(
        f"domain: {', '.join(domains)} ({basis_note})" if domains else f"domain: UNMAPPED ({basis_note})")
    mapping = _registry_domains()
    out: list[Record] = []
    seen: set[str] = set()
    # primary channel: domain-mapped sections, registry order, dedup across domains
    for d in domains:
        for anchor_ref in mapping.get(d, []):
            if len(out) >= qc.budget:
                break
            section = idx.resolve(anchor_ref)
            if section is None:
                bundle.orientation.append(f"section_moved: {anchor_ref} (stale mapping — lint should fail)")
                continue
            if section.section_anchor in seen:
                continue
            seen.add(section.section_anchor)
            out.append(DocSectionRecord(
                ref=f"{section.doc_path}#{section.anchor8}", kind="normative_section",
                summary=section.excerpt, backend=qc.backend, class_id=qc.class_id,
                path=f"{section.doc_path} / {section.heading_path}",
                doc_path=section.doc_path, heading_path=section.heading_path,
                content_digest=section.content_digest, commit=idx.commit, basis="domain"))
    # secondary channel: lexical ADDITIONS over anchor text terms vs section headings —
    # labeled basis:lexical, never counted as domain coverage (#156 sketch 2)
    text = (b.unbound_text or b.intent.text_terms or "")
    terms = {w for w in (t.strip(".,;:()`'\"").lower() for t in text.split())
             if len(w) >= 4 and w not in _STOPWORDS}
    if terms and len(out) < qc.budget:
        for section in idx.sections:
            if len(out) >= qc.budget:
                break
            if section.section_anchor in seen:
                continue
            hay = section.heading.lower()
            if any(t in hay for t in terms):
                seen.add(section.section_anchor)
                out.append(DocSectionRecord(
                    ref=f"{section.doc_path}#{section.anchor8}", kind="normative_section",
                    summary=section.excerpt, backend=qc.backend, class_id=qc.class_id,
                    path=f"{section.doc_path} / {section.heading_path}",
                    doc_path=section.doc_path, heading_path=section.heading_path,
                    content_digest=section.content_digest, commit=idx.commit, basis="lexical"))
    return out


def _exec_precedent(conn: psycopg.Connection, plan: RetrievalPlan, qc: QueryClass,
                    bundle: Bundle, viewer_scopes: frozenset[str]) -> list[Record]:
    """#156 sketch 9: prior outcomes from INTERNAL authoritative state only — the
    event log's result.recorded rows for the anchor's work/plan lineage. The source
    boundary is stated; GitHub-resident discussion is a measured miss (Phase B)."""
    b = plan.bound
    scopes = list(viewer_scopes)
    bundle.orientation.append("precedent source: event log (internal); external discussion threads not consulted")
    ev_in = _IN_SCOPE.format(a="e")
    out: list[Record] = []
    with conn.cursor() as cur:
        if b.anchor_kind == "work":
            work_refs = [b.anchor_ref]
        elif b.anchor_kind == "plan":
            cur.execute("SELECT DISTINCT work_ref FROM event_work WHERE plan_ref=%s "
                        "ORDER BY work_ref", (b.anchor_ref,))
            work_refs = [r[0] for r in cur.fetchall()]
        else:
            work_refs = []
        for wr in work_refs:
            if len(out) >= qc.budget:
                break
            cur.execute(
                "SELECT r.event_id, r.outcome FROM event_result r "
                "JOIN events e ON e.event_id=r.event_id "
                f"WHERE r.work_ref=%s AND {ev_in} "
                "ORDER BY e.origin_node, e.origin_seq DESC LIMIT %s",
                (wr, scopes, qc.budget - len(out)))
            for eid, outcome in cur.fetchall():
                out.append(Record(ref=eid, kind="precedent", summary=f"{wr}: {outcome}",
                                  backend=qc.backend, class_id=qc.class_id,
                                  path=f"{b.anchor_ref} ~precedent~ {wr}"))
    return out


_EXECUTORS = {
    "anchor_lookup": _exec_anchor,
    "standing": _exec_standing,
    "evidence": _exec_evidence,
    "neighborhood": _exec_neighborhood,
    "lexical": _exec_lexical,
    "repository_normative": _exec_repository_normative,
    "precedent": _exec_precedent,
}
