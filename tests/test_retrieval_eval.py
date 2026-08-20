"""Step 11 PR 11A-1 acceptance (#122): the recall gate's machinery — corpus quotas (BC-3),
unreachability witnesses (BC-1), double-blind arithmetic (BC-2), verdict phases, the
prose-override guard (r1 (j)). The corpus HERE is synthetic on purpose: these tests verify
the instrument's mechanics; the real gate corpus is real-log-sourced by plan.
"""
from __future__ import annotations

import json

import pytest

from kawa.application.services import Kawa
from kawa.domain.events import ClaimRecorded, LinkAsserted, PlanCreated
from kawa.domain.identity import IdentityContext
from kawa.retrieval_eval import (
    CLASSES,
    blind_export,
    guard_result,
    lexical_witness,
    machine_cause,
    measure,
    traversal_witness,
    validate_corpus,
)

psycopg = pytest.importorskip("psycopg")

from tests.test_archive import conn_a, _fresh  # noqa: E402,F401


@pytest.fixture()
def seeded(conn_a):  # type: ignore[no-untyped-def]
    """A tiny real log: a plan, a supporting claim (linked), and an isolated claim that no
    lexical/structural path reaches from anywhere — the semantic-candidate substrate."""
    k = Kawa(conn_a, identity=IdentityContext.from_local_runtime(node_ref="t", actor_ref="t"),
             default_scope=None)
    plan_ev = k.create_plan("p1", "kawa", "alpha beta objective")
    claim_ev = k.record_claim("gamma delta proposition")
    k.assert_link(claim_ev.event_id, "supports", plan_ev.event_id)
    isolated = k.record_claim("zeta omega unrelated")
    conn_a.commit()
    return k, plan_ev, claim_ev, isolated


def _q(qid, cls, sampling, labels, about=None, text=None):  # type: ignore[no-untyped-def]
    return {"query_id": qid, "expected_class": cls, "sampling": sampling,
            # provenance spells the private coordinate the way the real corpus
            # does (`github:<owner>/<repo>#N`), not as a URL: the published
            # mirror carries this file, and a link into the private repository
            # is a 404 for every reader. See the private-repo rule in
            # scripts/lint_publication_boundary.py.
            "labels": labels, "provenance": "github:unagikudari/kawa#122",
            "labeled_by": "test", "labeled_at": "2026-08-15T00:00:00Z",
            "intent": {"about": about, "text_terms": text}}


def _corpus(plan_ev, claim_ev, isolated, *, poison_lexical=False):  # type: ignore[no-untyped-def]
    """40 queries, quota-satisfying: 8 per class; >=1/3 failure_sourced, >=1/4 cross_reference.
    poison_lexical: half the lexical class's labels become the unreachable isolated claim —
    recall 0.5 (< bar, survives one-label flip), misses all machine-semantic."""
    queries = []
    sampling_cycle = ["failure_sourced", "cross_reference", "failure_sourced", "found"]
    for i, cls in enumerate(c for c in CLASSES for _ in range(8)):
        n = len(queries)
        s = sampling_cycle[n % 4]
        if cls == "anchor_lookup":
            # labels live in retrieve()'s REF SPACE: a plan anchor answers as its plan_ref
            q = _q(f"q{n}", cls, s, ["p1"], about="p1")
        elif cls == "standing":
            q = _q(f"q{n}", cls, s, [claim_ev.event_id], about=claim_ev.event_id)
        elif cls == "evidence":
            q = _q(f"q{n}", cls, s, [claim_ev.event_id], about=plan_ev.event_id)
        elif cls == "neighborhood":
            q = _q(f"q{n}", cls, s, [claim_ev.event_id], about=plan_ev.event_id)
        else:  # lexical
            label = isolated.event_id if (poison_lexical and n % 2 == 0) else claim_ev.event_id
            q = _q(f"q{n}", cls, s, [label], text="gamma delta")
        queries.append(q)
    return {"corpus_version": 1, "queries": queries}


def test_corpus_quota_structure_check(seeded) -> None:  # type: ignore[no-untyped-def]
    k, plan_ev, claim_ev, isolated = seeded
    good = _corpus(plan_ev, claim_ev, isolated)
    assert validate_corpus(good) == []
    # class starvation
    bad = {"corpus_version": 1, "queries": good["queries"][:39]}
    assert any("quota: class" in v for v in validate_corpus(bad))
    # sampling starvation: all 'found'
    allfound = json.loads(json.dumps(good))
    for q in allfound["queries"]:
        q["sampling"] = "found"
    vs = validate_corpus(allfound)
    assert any("failure_sourced" in v for v in vs) and any("cross_reference" in v for v in vs)
    # missing provenance / labels
    noprov = json.loads(json.dumps(good))
    noprov["queries"][0]["provenance"] = ""
    assert any("provenance" in v for v in validate_corpus(noprov))


def test_witnesses_are_machine_proofs(conn_a, seeded) -> None:  # type: ignore[no-untyped-def]
    k, plan_ev, claim_ev, isolated = seeded
    # lexical: the REAL FTS predicate against one record
    assert lexical_witness(conn_a, "gamma", claim_ev.event_id)["reachable"] == "yes"
    assert lexical_witness(conn_a, "gamma", isolated.event_id)["reachable"] == "no"
    # traversal: shortest path IS the proof; depth binds it
    w = traversal_witness(conn_a, plan_ev.event_id, claim_ev.event_id, depth=2)
    assert w["reachable"] == "yes" and w["witness"]["shortest_path"] == 1
    w0 = traversal_witness(conn_a, plan_ev.event_id, claim_ev.event_id, depth=0)
    assert w0["reachable"] == "budget" and w0["witness"]["shortest_path"] == 1
    assert traversal_witness(conn_a, plan_ev.event_id, isolated.event_id, 5)["reachable"] == "no"
    # the cause derivation: any 'yes' is NEVER semantic
    assert machine_cause([{"reachable": "yes"}, {"reachable": "no"}]) == "plan_or_budget"
    assert machine_cause([{"reachable": "budget"}, {"reachable": "no"}]) == "budget"
    assert machine_cause([{"reachable": "no"}, {"reachable": "no"}]) == "semantic_candidate"


def test_verdict_phases_and_double_blind_arithmetic(conn_a, seeded) -> None:  # type: ignore[no-untyped-def]
    k, plan_ev, claim_ev, isolated = seeded
    corpus = _corpus(plan_ev, claim_ev, isolated, poison_lexical=True)
    raw = json.dumps(corpus).encode()
    # phase 1: semantic candidates exist, no adjudication -> the gate CANNOT close (BC-2)
    r1 = measure(conn_a, corpus, raw)
    assert r1["verdict"] == "PENDING_ADJUDICATION"
    lex = r1["classes"]["lexical"]
    assert lex["sub_bar"] and lex["semantic_candidates"]
    # phase 2a: reviewer AGREES on every candidate -> the counted (agreed-only) misses push
    # the class under the bar, surviving a one-label flip -> GO
    agree = {mk: "semantic" for mk in lex["semantic_candidates"]}
    r2 = measure(conn_a, corpus, raw, agree)
    assert r2["verdict"] == "GO" and r2["go_classes"] == ["lexical"]
    assert r2["classes"]["lexical"]["survives_one_label_flip"]
    # phase 2b: one dispute -> that miss is EXCLUDED from the counted set; the remaining
    # agreed misses no longer survive the one-label flip -> NO_GO (BC-2 + rev 2 (d))
    dispute = dict(agree)
    dispute[lex["semantic_candidates"][0]] = "budget"
    r3 = measure(conn_a, corpus, raw, dispute)
    assert r3["verdict"] == "NO_GO"
    assert not r3["classes"]["lexical"]["survives_one_label_flip"]
    # healthy corpus: NO_GO with no pending anything
    healthy = _corpus(plan_ev, claim_ev, isolated)
    rh = measure(conn_a, healthy, json.dumps(healthy).encode())
    assert rh["verdict"] == "NO_GO"
    assert all(not c["sub_bar"] for c in rh["classes"].values()), \
        {k: v for k, v in rh["classes"].items() if v["sub_bar"]}
    # corpus pinning: a one-byte corpus change changes the digest
    assert r1["corpus_digest"] != rh["corpus_digest"]


def test_blind_export_strips_causes_and_guard_refuses_prose(conn_a, seeded) -> None:  # type: ignore[no-untyped-def]
    k, plan_ev, claim_ev, isolated = seeded
    corpus = _corpus(plan_ev, claim_ev, isolated, poison_lexical=True)
    report = measure(conn_a, corpus, json.dumps(corpus).encode())
    package = blind_export(report)
    assert package and all("machine_cause" not in m for m in package)     # BC-2: labels hidden
    assert all("per_backend_unreachability" in m for m in package)        # witnesses shared
    with pytest.raises(ValueError, match="override is a failure"):
        guard_result(report, "NO_GO")                                     # prose != machine (j)
    guard_result(report, "PENDING_ADJUDICATION")                          # citing truth is fine
    # PR #123 review hardening: a typo'd adjudication key is LOUD, never a silent PENDING pin
    bad = measure(conn_a, corpus, json.dumps(corpus).encode(), {"q999::nope": "semantic"})
    assert bad["verdict"] == "INVALID_ADJUDICATION" and bad["unknown_miss_keys"]