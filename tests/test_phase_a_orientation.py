"""#156 Phase A — principle-aware internal orientation: the four-fixture acceptance
gate (sketch 8) plus the mechanism invariants (budget regression, limit algebra,
envelope compatibility, emit validation, manifest equivalence).

Episode fixtures are LOCKED, un-coached real texts (r2-F6 / r3-F5):
- tests/fixtures/episode1_fork_resolution.md  == the verbatim pre-reminder body of
  the fork-resolution proposal (the episode whose principle miss Phase A mechanizes
  away). It proposes truncate/replace and never says "append-only".
- tests/fixtures/episode2_scope_disclosure.md == the verbatim ADV-02 retrieval-scope
  finding as filed.
No solution keywords are injected into fixture Work/Plan/Claim texts; assertions
require `basis: domain` — a lexical rescue is not success.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.retrieval import FLEET_SCOPES as FLEET
from kawa.retrieval import DocSectionRecord, Intent, compile_plan, resolve_bindings, retrieve

psycopg = pytest.importorskip("psycopg")

_FIXTURES = Path(__file__).parent / "fixtures"
EPISODE1 = (_FIXTURES / "episode1_fork_resolution.md").read_text()
EPISODE2 = (_FIXTURES / "episode2_scope_disclosure.md").read_text()

# the commit both episodes' target sections are pinned to (episode era; r3-F5 stage 1)
PINNED_COMMIT = "41cf8e5"

_ALL = (
    "content_embedding, event_content, events, event_links, event_link, event_observation, "
    "event_claim, event_plan, event_work, event_work_dependency, event_work_retired, "
    "event_result, current_claim_standing, current_plans, current_work, "
    "current_work_dependency, runtime_work_occupancy, work_dispatch"
)


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a"),
                            autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def k(conn):  # type: ignore[no-untyped-def]
    return Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))


def _normative(bundle):  # type: ignore[no-untyped-def]
    return [r for recs in bundle.sections.values() for r in recs
            if getattr(r, "kind", "") == "normative_section"]


def _domain_normative(bundle):  # type: ignore[no-untyped-def]
    return [r for r in _normative(bundle) if getattr(r, "basis", "") == "domain"]


# ---- acceptance fixture 1: episode #147 (fork resolution, un-coached) ----

def test_episode1_fork_resolution_surfaces_event_log_invariants(conn, k) -> None:  # type: ignore[no-untyped-def]
    """The real pre-reminder text proposes truncate/replace and never names the
    invariants. Domain derives STRUCTURALLY (owning Plan's token, the real shape:
    the proposal lived under event-log work) — and the append-only and
    corrections-as-later-Events sections must be in the top-3 domain candidates."""
    k.create_plan("plan-fork-fix", "proj", "resolve fork adoption limitation",
                  domain="event_store")
    k.derive_work("w-fork-fix", "plan-fork-fix", "implement", objective=EPISODE1)
    bundle = retrieve(conn, Intent(about="w-fork-fix"), viewer_scopes=FLEET)

    top3 = _domain_normative(bundle)[:3]
    assert top3, "domain-mapped normative candidates must exist"
    hit_paths = {r.doc_path for r in top3}
    assert "docs/event-log-and-replication-v0.1.md" in hit_paths        # append-only log
    assert "docs/specification-v0.5.md" in hit_paths                    # corrections create later Events
    for r in top3:
        assert r.basis == "domain"                                      # never a lexical rescue
        assert len(r.summary) <= 322                                    # bounded excerpt (cap + marker)
        assert r.content_digest and r.commit                            # provenance pinned
    # co-existence under the default limit: structural classes are not displaced
    assert bundle.sections.get("q0-anchor_lookup"), "anchor record must co-exist"
    assert any("domain: event_store" in o for o in bundle.orientation)


# ---- acceptance fixture 2: episode ADV-02 (scope disclosure, un-coached) ----

def test_episode2_scope_disclosure_surfaces_disclosure_invariants(conn, k) -> None:  # type: ignore[no-untyped-def]
    plan_ev = k.create_plan("plan-scope-fix", "proj", "review retrieval boundary",
                            domain="retrieval")
    finding = k.record_claim(EPISODE2)
    k.assert_link(finding.event_id, "based_on", plan_ev.event_id)
    bundle = retrieve(conn, Intent(about=finding.event_id), viewer_scopes=FLEET)

    top3 = _domain_normative(bundle)[:3]
    hit = {(r.doc_path, r.heading_path.split(" / ")[-1]) for r in top3}
    assert any(p == "docs/security-model-v0.1.md" for p, _ in hit)      # authorize before disclosure
    assert any(p == "docs/scope-resolution-v0.1.md" for p, _ in hit)    # omission never selects scope
    assert all(r.basis == "domain" for r in top3)


def test_episode2_two_stage_pinning() -> None:  # type: ignore[no-untyped-def]
    """r3-F5: stage 1 — the target sections resolve at the episode-era pinned
    commit; stage 2 — the same anchors still resolve at current HEAD (continuity
    under doc evolution; a rename would surface here as a stale mapping)."""
    from kawa.repo_sections import build_index
    repo = Path(__file__).resolve().parents[1]
    anchors = [
        "docs/security-model-v0.1.md#2. Security invariants",
        "docs/scope-resolution-v0.1.md#2. Core rule",
        "docs/event-log-and-replication-v0.1.md#4. The per-node log and the replication frontier",
    ]
    try:
        pinned = build_index(repo, PINNED_COMMIT)
    except Exception as exc:  # pragma: no cover — shallow clone etc.
        pytest.skip(f"pinned commit unavailable: {exc}")
    head = build_index(repo)
    for a in anchors:
        assert pinned.resolve(a) is not None, f"stage 1 (pinned): {a}"
        assert head.resolve(a) is not None, f"stage 2 (HEAD): {a}"


# ---- acceptance fixture 3: negative/control — structure carries, not keywords ----

def test_control_keyword_free_anchor_still_resolves_by_structure(conn, k) -> None:  # type: ignore[no-untyped-def]
    k.create_plan("plan-neutral", "proj", "proceed with the item", domain="event_store")
    k.derive_work("w-neutral", "plan-neutral", "implement",
                  objective="proceed with the item as specified")     # zero registry terms
    bundle = retrieve(conn, Intent(about="w-neutral"), viewer_scopes=FLEET)
    assert _domain_normative(bundle), "domain channel must not depend on anchor keywords"


# ---- acceptance fixture 4: frontier — UNMAPPED is stated, never fabricated ----

def test_frontier_unmapped_domain_is_stated_not_fabricated(conn, k) -> None:  # type: ignore[no-untyped-def]
    k.create_plan("plan-legacy", "proj", "legacy plan without a domain token")
    k.derive_work("w-legacy", "plan-legacy", "implement",
                  objective="investigate the replication frontier lag")   # lexical bait on purpose
    bundle = retrieve(conn, Intent(about="w-legacy"), viewer_scopes=FLEET)
    assert any(o.startswith("domain: UNMAPPED") for o in bundle.orientation)
    assert not _domain_normative(bundle), "no fabricated domain candidates"
    # lexical additions may exist via Intent text only — the Work objective is NOT
    # a lexical source; an anchored ref-only intent has no caller text at all
    assert all(getattr(r, "basis", "") == "lexical" for r in _normative(bundle))


# ---- precedent: internal-only source; synthetic events labeled (r3-F6) ----

def test_precedent_from_internal_event_log_only(conn, k) -> None:  # type: ignore[no-untyped-def]
    """Synthetic precedent events injected into the TEST DB, shaped from the real
    review flow (verdict Results recorded against a work) — labeled synthetic here,
    never counted as production recall coverage (r3-F6/r4-F6)."""
    k.create_plan("plan-p", "proj", "plan with history", domain="event_store")
    k.derive_work("w-p", "plan-p", "implement")
    k.record_result("w-p", "failure", "sha256:" + "a" * 64,
                    summary="synthetic: rejected approach (shaped from real review flow)")
    bundle = retrieve(conn, Intent(about="w-p"), viewer_scopes=FLEET)
    prec = [r for recs in bundle.sections.values() for r in recs if r.kind == "precedent"]
    assert prec and "failure" in prec[0].summary
    assert any(o.startswith("precedent source: event log") for o in bundle.orientation)


# ---- budget regression + limit algebra (sketch 6 / r3-F4) ----

def test_the_sideband_lives_inside_the_ceiling(conn, k) -> None:  # type: ignore[no-untyped-def]
    """RE-BASELINED, deliberately (#214 step 2).

    This test used to assert that the Phase-A sideband was ADDITIVE — structural
    budgets byte-identical to pre-Phase-A, and `total <= limit + 9 + n`. That
    algebra is the defect: it is how `Intent(limit=0)` authorised fourteen records,
    and no caller-visible number described the total.

    The sideband is now an ordinary member of the tiers, inside the ceiling. It
    keeps its full 6+3 whenever the ceiling can afford them — a caller who sets a
    small limit loses REACH, not GROUNDING — and the default rose to 59 so callers
    who set nothing keep the budget they had.
    """
    c = k.record_claim("x")

    # affordable: the sideband still gets exactly what the profile asks for
    plan = compile_plan(resolve_bindings(conn, Intent(about=c.event_id, limit=59), FLEET))
    sideband = [(q.purpose, q.budget) for q in plan.query_classes
                if q.purpose in ("repository_normative", "precedent")]
    assert sideband == [("repository_normative", 6), ("precedent", 3)]
    assert plan.total_budget <= 59

    # tight: grounding survives, reach does not
    tight = compile_plan(resolve_bindings(conn, Intent(about=c.event_id, limit=3), FLEET))
    assert [q.purpose for q in tight.query_classes] == [
        "anchor_lookup", "standing", "repository_normative"]
    assert tight.total_budget <= 3
    assert {s.purpose for s in tight.skipped_at_compile} == {
        "evidence", "neighborhood", "precedent", "vector"}
    assert all(s.reason == "tier_budget_exhausted" for s in tight.skipped_at_compile)


def test_unanchored_textual_intent_plans_no_sideband(conn, k) -> None:  # type: ignore[no-untyped-def]
    plan = compile_plan(resolve_bindings(conn, Intent(text_terms="anything"), FLEET))
    assert all(q.purpose not in ("repository_normative", "precedent")
               for q in plan.query_classes)


# ---- emit validation (fail-closed, r3-F1) + digest stability ----

def test_unknown_domain_token_rejected_at_write(conn, k) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="unknown domain token"):
        k.create_plan("plan-bad", "proj", "x", domain="not-a-real-domain")
    with pytest.raises(ValueError, match="unknown domain token"):
        k.derive_work("w-bad", "plan-p", "implement", domain="free text here")


def test_absent_domain_keeps_payload_digest_shape(conn, k) -> None:  # type: ignore[no-untyped-def]
    """Additive-field rule (#102 round-2 constraint 2): a None domain must not
    appear in the payload dump, so pre-Phase-A event digests re-derive unchanged."""
    from kawa.domain.events import PlanCreated, WorkDerived
    dump = PlanCreated(plan_ref="p", project_ref="pr", objective="o").model_dump()
    assert "domain" not in dump
    dump2 = WorkDerived(work_ref="w", plan_ref="p", work_kind="implement").model_dump()
    assert "domain" not in dump2
    assert PlanCreated(plan_ref="p", project_ref="pr", objective="o",
                       domain="event_store").model_dump()["domain"] == "event_store"


# ---- envelope compatibility (sketch 7) ----

def test_doc_section_record_satisfies_record_contract(conn, k) -> None:  # type: ignore[no-untyped-def]
    k.create_plan("plan-env", "proj", "x", domain="retrieval")
    k.derive_work("w-env", "plan-env", "implement")
    bundle = retrieve(conn, Intent(about="w-env"), viewer_scopes=FLEET)
    secs = _normative(bundle)
    assert secs
    r = secs[0]
    assert isinstance(r, DocSectionRecord)
    for shared in ("ref", "kind", "summary", "backend", "class_id", "path",
                   "path_truncated", "standing"):
        assert hasattr(r, shared)                       # Record field contract holds
    assert r.ref == f"{r.doc_path}#{r.ref.split('#')[1]}" and len(r.ref.split("#")[1]) == 8
    b2 = retrieve(conn, Intent(about="w-env"), viewer_scopes=FLEET)
    assert bundle.sections == b2.sections               # dataclass equality / determinism


# ---- manifest equivalence (sketch 4 / r3-F3) ----

def test_manifest_and_checkout_paths_are_byte_identical() -> None:  # type: ignore[no-untyped-def]
    from kawa.repo_sections import build_index, load_manifest
    repo = Path(__file__).resolve().parents[1]
    idx = build_index(repo)
    again = load_manifest(idx.to_manifest())
    assert again.commit == idx.commit and again.source == "manifest"
    assert again.sections == idx.sections               # byte-identical candidates
