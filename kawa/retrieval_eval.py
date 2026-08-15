"""The recall gate (#122 11A — the machine half of §10.6's conditional).

Everything a human could fudge is a computed artifact here (r1 (a)-(j) + BC-1..BC-6):

  corpus            versioned, digested, quota-checked (BC-3) — the gate is pinned to bytes
  witnesses         per-miss, per-backend MACHINE proofs of (un)reachability (BC-1) — a
                    "semantic miss" is a computed property, not a diagnostician's feeling
  blind export      the BC-2 double-blind protocol's instrument: misses stripped of the
                    author's cause labels for independent re-diagnosis; only AGREED
                    semantic misses enter the GO arithmetic; disputes are excluded
  verdict           computed (GO | NO_GO | PENDING_ADJUDICATION), never prose; a Result
                    claiming otherwise is refused by `guard_result` (r1 (j))

The bar (pinned by the approved plan, #122 rev 2 (d)): a class may trigger GO only with
n >= 8 queries, micro-recall < 0.75, the decision surviving a one-label flip, and every
counted miss machine-semantic AND double-blind-agreed. Anything less is NO_GO — and NO_GO
completes roadmap step 11 (§10.6's additional condition machine-judged false).
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field

import psycopg

from kawa.retrieval import Intent, resolve_bindings, retrieve

CLASSES = ("anchor_lookup", "standing", "evidence", "neighborhood", "lexical")  # BC-3 canonical
SAMPLINGS = ("failure_sourced", "cross_reference", "found")
RECALL_BAR = 0.75
MIN_CLASS_N = 8
_BFS_NODE_CAP = 20_000     # witness explorations answer, never hang; the cap is reported


# ---- corpus (BC-3) ----

def corpus_digest(raw_bytes: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw_bytes).hexdigest()


def validate_corpus(corpus: dict) -> list[str]:
    """Structure + quota violations (empty = valid). Runs BEFORE any measurement — a corpus
    failing quotas cannot produce a verdict at all (the sampling-bias gate, rev 2 (e))."""
    v: list[str] = []
    queries = corpus.get("queries", [])
    if not isinstance(queries, list) or not queries:
        return ["corpus has no queries"]
    seen_ids: set[str] = set()
    per_class: dict[str, int] = {c: 0 for c in CLASSES}
    per_sampling: dict[str, int] = {s: 0 for s in SAMPLINGS}
    for i, q in enumerate(queries):
        qid = q.get("query_id")
        if not qid or qid in seen_ids:
            v.append(f"query[{i}]: missing or duplicate query_id")
            continue
        seen_ids.add(qid)
        if q.get("expected_class") not in CLASSES:
            v.append(f"{qid}: expected_class must be one of {CLASSES}")
        if q.get("sampling") not in SAMPLINGS:
            v.append(f"{qid}: sampling must be one of {SAMPLINGS}")
        if not q.get("labels"):
            v.append(f"{qid}: no labels (a query without relevant records measures nothing)")
        elif len(q["labels"]) != len(set(q["labels"])):
            v.append(f"{qid}: duplicate labels")
        if not q.get("provenance"):
            v.append(f"{qid}: no provenance link (BC-3: where this query came from, checkable)")
        if not q.get("labeled_by") or not q.get("labeled_at"):
            v.append(f"{qid}: labeling provenance (labeled_by/labeled_at) required")
        intent = q.get("intent", {})
        if not intent.get("about") and not intent.get("text_terms"):
            v.append(f"{qid}: intent binds nothing (about/text_terms both empty)")
        if q.get("expected_class") in per_class:
            per_class[q["expected_class"]] += 1
        if q.get("sampling") in per_sampling:
            per_sampling[q["sampling"]] += 1
    n = len(queries)
    for c in CLASSES:
        if per_class[c] < MIN_CLASS_N:
            v.append(f"quota: class {c} has {per_class[c]} < {MIN_CLASS_N} queries (BC-3)")
    if per_sampling["failure_sourced"] * 3 < n:
        v.append(f"quota: failure_sourced {per_sampling['failure_sourced']}/{n} < 1/3 (rev 2 (e))")
    if per_sampling["cross_reference"] * 4 < n:
        v.append(f"quota: cross_reference {per_sampling['cross_reference']}/{n} < 1/4 (rev 2 (e))")
    return v


# ---- witnesses (BC-1): machine proofs, not prose ----

def lexical_witness(conn: psycopg.Connection, text_terms: str | None, record_id: str) -> dict:
    """Would the FTS predicate itself match this record? Runs the REAL predicate against the
    one record — reachable=yes means the miss is a plan/budget problem, never semantic."""
    if not text_terms:
        return {"backend": "fts", "reachable": "no", "witness": "query has no text terms"}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT (SELECT to_tsvector('english', proposition) @@ websearch_to_tsquery('english', %s) "
            "        FROM event_claim WHERE event_id = %s), "
            "       (SELECT to_tsvector('english', objective) @@ websearch_to_tsquery('english', %s) "
            "        FROM event_plan WHERE event_id = %s AND objective IS NOT NULL)",
            (text_terms, record_id, text_terms, record_id))
        claim_hit, plan_hit = cur.fetchone()
    hit = bool(claim_hit) or bool(plan_hit)
    return {"backend": "fts", "reachable": "yes" if hit else "no",
            "witness": {"tsquery_matches_record": hit}}


def traversal_witness(conn: psycopg.Connection, anchor: str | None, record_id: str,
                      depth: int) -> dict:
    """BFS over resolved event_links (both directions) from the anchor: the shortest path
    length IS the proof. Within depth => reachable (a budget/row-cap miss); beyond => the
    length is stated; no path => unreachable. Node-capped, cap reported."""
    if anchor is None:
        return {"backend": "traversal", "reachable": "no", "witness": "no bound anchor"}
    with conn.cursor() as cur:
        cur.execute("SELECT source_ref, target_ref FROM event_links WHERE resolved "
                    "ORDER BY source_ref, target_ref")
        edges = cur.fetchall()
    adj: dict[str, list[str]] = {}
    for s, t in edges:
        adj.setdefault(s, []).append(t)
        adj.setdefault(t, []).append(s)
    for k in adj:                                     # PR #123 review: fully deterministic
        adj[k] = sorted(set(adj[k]))                  # expansion order, cap-safe
    dist = {anchor: 0}
    dq = deque([anchor])
    capped = False
    while dq:
        node = dq.popleft()
        if node == record_id:
            break
        if len(dist) > _BFS_NODE_CAP:
            capped = True
            break
        for nxt in adj.get(node, ()):
            if nxt not in dist:
                dist[nxt] = dist[node] + 1
                dq.append(nxt)
    k = dist.get(record_id)
    if k is None:
        return {"backend": "traversal", "reachable": "no",
                "witness": {"shortest_path": None, "explored_capped": capped}}
    return {"backend": "traversal", "reachable": "yes" if k <= depth else "budget",
            "witness": {"shortest_path": k, "depth": depth, "explored_capped": capped}}


def typed_sql_witness(conn: psycopg.Connection, anchor: str | None, record_id: str) -> dict:
    """Anchor/standing/evidence reachability: is the record the anchor, its standing row's
    subject, or a resolved supports/contradicts source touching the anchor?"""
    if anchor is None:
        return {"backend": "typed_sql", "reachable": "no", "witness": "no bound anchor"}
    if record_id == anchor:
        return {"backend": "typed_sql", "reachable": "yes", "witness": {"is_anchor": True}}
    with conn.cursor() as cur:
        # BOTH directions, matching _exec_evidence (PR #123 review: an outward evidence
        # edge must not read as unreachable)
        cur.execute("SELECT relation FROM event_links WHERE resolved "
                    "AND relation IN ('supports','contradicts') "
                    "AND ((target_ref=%s AND source_ref=%s) OR (source_ref=%s AND target_ref=%s))",
                    (anchor, record_id, anchor, record_id))
        row = cur.fetchone()
    if row:
        return {"backend": "typed_sql", "reachable": "yes",
                "witness": {"evidence_edge": row[0]}}
    return {"backend": "typed_sql", "reachable": "no", "witness": {"no_typed_relation": True}}


def resolve_label_events(conn: psycopg.Connection, label: str) -> list[str]:
    """Labels live in the REF SPACE retrieve() answers in (event ids, plan_refs, work_refs).
    Witnesses run over event ids — a plan/work label resolves to its underlying events, and
    a witness's answer for the label is the MOST reachable answer across them (fail toward
    'this was reachable', never toward a spurious semantic candidate)."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM events WHERE event_id=%s", (label,))
        if cur.fetchone():
            return [label]
        cur.execute("SELECT event_id FROM event_plan WHERE plan_ref=%s", (label,))
        rows = [r[0] for r in cur.fetchall()]
        if rows:
            return rows
        cur.execute("SELECT event_id FROM event_work WHERE work_ref=%s", (label,))
        return [r[0] for r in cur.fetchall()]


_REACH_ORDER = {"yes": 2, "budget": 1, "no": 0}


def _best(witnesses: list[dict]) -> dict:
    return max(witnesses, key=lambda w: _REACH_ORDER.get(w["reachable"], 0))


def machine_cause(witnesses: list[dict]) -> str:
    """The BC-1 derivation: any backend 'yes' => plan_or_budget (NOT semantic — the record
    was reachable, the pipeline just didn't surface it); any 'budget' => budget; all 'no'
    => semantic_candidate (double-blind adjudication decides whether it COUNTS, BC-2)."""
    kinds = {w["reachable"] for w in witnesses}
    if "yes" in kinds:
        return "plan_or_budget"
    if "budget" in kinds:
        return "budget"
    return "semantic_candidate"


# ---- the harness ----

@dataclass
class ClassReport:
    n: int = 0
    labels_total: int = 0
    labels_hit: int = 0
    misses: list[dict] = field(default_factory=list)

    def micro_recall(self) -> float | None:
        return (self.labels_hit / self.labels_total) if self.labels_total else None


def measure(conn: psycopg.Connection, corpus: dict, raw_bytes: bytes,
            adjudication: dict | None = None, embedder=None) -> dict:
    """Run the corpus through the REAL retrieve() and compute the verdict. Deterministic
    given (log state, corpus bytes, adjudication). `adjudication` is the BC-2 merge:
    {miss_key: reviewer_cause}; absent => the verdict is PENDING_ADJUDICATION whenever
    semantic candidates exist (the gate cannot close on one person's judgment)."""
    violations = validate_corpus(corpus)
    if violations:
        return {"verdict": "INVALID_CORPUS", "violations": violations,
                "corpus_digest": corpus_digest(raw_bytes)}
    if adjudication:
        # PR #123 review: an unknown miss_key (typo) must be LOUD — silently ignoring it
        # would leave a real candidate un-adjudicated and pin the gate at PENDING forever
        valid_keys = {f"{q['query_id']}::{label}"
                      for q in corpus["queries"] for label in q["labels"]}
        unknown = sorted(set(adjudication) - valid_keys)
        if unknown:
            return {"verdict": "INVALID_ADJUDICATION", "unknown_miss_keys": unknown,
                    "corpus_digest": corpus_digest(raw_bytes)}
    per_class: dict[str, ClassReport] = {c: ClassReport() for c in CLASSES}
    for q in corpus["queries"]:
        spec = q["intent"]
        intent = Intent(about=spec.get("about"), text_terms=spec.get("text_terms"),
                        relation_depth=spec.get("relation_depth", 2),
                        limit=spec.get("limit", 50),
                        fallback_policy=spec.get("fallback_policy"))
        bundle = retrieve(conn, intent, embedder)     # 11B: None => textual vector stated as skipped
        retrieved = {r.ref for recs in bundle.sections.values() for r in recs}
        anchor = bundle.plan.bound.anchor_ref
        rep = per_class[q["expected_class"]]
        rep.n += 1
        rep.labels_total += len(q["labels"])
        for label in q["labels"]:
            if label in retrieved:
                rep.labels_hit += 1
                continue
            targets = resolve_label_events(conn, label) or [label]
            witnesses = [
                _best([typed_sql_witness(conn, anchor, t) for t in targets]),
                _best([traversal_witness(conn, anchor, t, intent.relation_depth)
                       for t in targets]),
                _best([lexical_witness(conn, intent.text_terms, t) for t in targets]),
            ]
            rep.misses.append({
                "miss_key": f"{q['query_id']}::{label}",
                "query_id": q["query_id"],
                "expected_record": label,
                "retrieved_ids": sorted(retrieved),
                "plan": [f"{qc.class_id}:{qc.backend}:b{qc.budget}"
                         for qc in bundle.plan.query_classes],
                "skipped_classes": bundle.skipped_classes,
                "frontier_reasons": sorted({f.reason for f in bundle.traversal_frontier}),
                "per_backend_unreachability": witnesses,
                "machine_cause": machine_cause(witnesses),
            })
    classes_out: dict[str, dict] = {}
    go_classes: list[str] = []
    pending = False
    for c, rep in per_class.items():
        recall = rep.micro_recall()
        sub_bar = recall is not None and recall < RECALL_BAR
        semantic = [m for m in rep.misses if m["machine_cause"] == "semantic_candidate"]
        agreed, disputed = [], []
        for m in semantic:
            reviewer = (adjudication or {}).get(m["miss_key"])
            if reviewer is None:
                pending = True
            elif reviewer == "semantic":
                agreed.append(m["miss_key"])           # BC-2: only agreement counts
            else:
                disputed.append(m["miss_key"])
        # The GO arithmetic counts ONLY agreed-semantic misses (rev 2: a budget miss is a
        # §10 tuning bug; a disputed miss is excluded, BC-2). counted_recall asks: do the
        # agreed-semantic misses ALONE push the class under the bar — and does that survive
        # one flipped label (rev 2 (d) sensitivity, applied to the counted set)?
        counted = len(agreed)
        counted_recall = (1 - counted / rep.labels_total) if rep.labels_total else None
        counted_flip = (1 - (counted - 1) / rep.labels_total) \
            if rep.labels_total and counted else None
        counted_sub_bar = counted_recall is not None and counted_recall < RECALL_BAR
        survives_flip = counted_sub_bar and counted_flip is not None and counted_flip < RECALL_BAR
        eligible = rep.n >= MIN_CLASS_N and counted > 0 and counted_sub_bar and survives_flip
        classes_out[c] = {
            "n": rep.n, "insufficient_n": rep.n < MIN_CLASS_N,
            "labels_total": rep.labels_total, "labels_hit": rep.labels_hit,
            "micro_recall": recall, "sub_bar": sub_bar,
            "counted_recall": counted_recall, "counted_one_label_flip": counted_flip,
            "counted_sub_bar": counted_sub_bar, "survives_one_label_flip": survives_flip,
            "semantic_candidates": [m["miss_key"] for m in semantic],
            "agreed_semantic": agreed, "disputed": disputed,
            "misses": rep.misses,
        }
        if eligible:
            go_classes.append(c)
    if pending and any(classes_out[c]["sub_bar"] for c in CLASSES):
        verdict = "PENDING_ADJUDICATION"
    else:
        verdict = "GO" if go_classes else "NO_GO"
    return {"verdict": verdict, "go_classes": go_classes, "bar": RECALL_BAR,
            "min_class_n": MIN_CLASS_N, "corpus_digest": corpus_digest(raw_bytes),
            "classes": classes_out}


def blind_export(report: dict) -> list[dict]:
    """The BC-2 instrument: every miss, stripped of cause fields, for independent
    re-diagnosis by a different agent from the same machine witnesses."""
    out = []
    for c in CLASSES:
        for m in report.get("classes", {}).get(c, {}).get("misses", []):
            blind = {k: v for k, v in m.items() if k != "machine_cause"}
            out.append(blind)
    return out


def guard_result(report: dict, claimed_verdict: str) -> None:
    """r1 (j): prose cannot override the machine — a Result claiming a verdict that differs
    from the computed one refuses to exist. Overrides need a new gate issue, never an edit."""
    if report.get("verdict") != claimed_verdict:
        raise ValueError(
            f"result/verdict divergence: harness computed {report.get('verdict')!r}, "
            f"result claims {claimed_verdict!r} — manual override is a failure of this step "
            "(#122 rev 2 (j)); open a new gate issue instead")
