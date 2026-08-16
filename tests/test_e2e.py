"""Phase 0 end-to-end integration test — requires the dedicated TEST database `kawa_test_a`.

Skips cleanly if the DB is unavailable, so `pytest` stays green in environments without it.

The fixture TRUNCATEs its target, so it must NEVER be pointed at the runtime/dogfood database:
it deliberately ignores `KAWA_DSN` (the runtime DSN) and connects via `KAWA_TEST_DSN_A` with a
`kawa_test_a` default. The dogfood log on this repo's own nodes already carries `origin_node=test`
scar tissue from the era when this fixture followed `KAWA_DSN` — that mistake is why this seam is
now a separate variable, not a convention.
"""
from __future__ import annotations

import os

import pytest

from kawa.application.services import Kawa
from kawa.domain.identity import IdentityContext
from kawa.projections.reducers import rebuild

psycopg = pytest.importorskip("psycopg")

_ALL = (
    "content_embedding, event_content, events, event_links, event_link, event_observation, event_claim, event_plan, "
    "event_work, event_work_dependency, event_work_retired, event_result, current_claim_standing, "
    "current_plans, current_work, current_work_dependency, runtime_work_occupancy"
)


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a"),
                            autocommit=False)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"kawa test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute(f"TRUNCATE {_ALL}")
    c.commit()
    yield c
    c.close()


def _snapshot(conn):  # type: ignore[no-untyped-def]
    with conn.cursor() as cur:
        cur.execute("SELECT plan_ref, lifecycle, end_reason FROM current_plans ORDER BY plan_ref")
        plans = cur.fetchall()
        cur.execute("SELECT work_ref, execution, dependency_total, dependency_satisfied, "
                    "dependency_conflicted FROM current_work ORDER BY work_ref")
        work = cur.fetchall()
    return {"plans": plans, "work": work}


def test_result_driven_readiness_loop(conn) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p1", "kawa", "loop")
    k.derive_work("impl", "p1", "implement", role_requirement="Implementer")
    k.derive_work("review", "p1", "review", role_requirement="Reviewer")
    k.declare_dependency("review", "impl", "ALL")

    assert k.work_state("impl") == "ready"
    assert k.work_state("review") == "blocked"           # blocked on evidence, not on an agent

    nxt = k.work_next("Implementer")
    assert nxt is not None and nxt["work"]["work_ref"] == "impl" and nxt["work"]["plan_ref"] == "p1"
    assert "instruction" in nxt and nxt["instruction_basis"]["renderer_version"]
    assert k.work_next("Reviewer") is None               # nothing ready for reviewer yet

    k.record_result("impl", "success", "r-impl")
    assert k.work_state("review") == "ready"             # Result unblocked the dependent Work
    assert k.work_next("Reviewer") is not None and k.work_next("Reviewer")["work"]["work_ref"] == "review"

    k.record_result("review", "success", "r-review")
    assert k.work_state("review") == "finished"


def test_conflicted_result_still_satisfies_but_flags(conn) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p2", "kawa", "conflict")
    k.derive_work("a", "p2", "implement")
    k.derive_work("b", "p2", "review")
    k.declare_dependency("b", "a", "ALL")
    k.record_result("a", "conflicted", "r-a")
    assert k.work_state("b") == "ready"                  # conflicted resolves the dependency (#53 §4)
    with conn.cursor() as cur:
        cur.execute("SELECT dependency_conflicted FROM current_work WHERE work_ref='b'")
        assert cur.fetchone()[0] == 1                    # ...but the conflict is surfaced, not hidden


def test_failed_dependency_blocks(conn) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p3", "kawa", "fail")
    k.derive_work("x", "p3", "implement")
    k.derive_work("y", "p3", "review")
    k.declare_dependency("y", "x", "ALL")
    k.record_result("x", "failure", "r-x")
    assert k.work_state("y") == "blocked"                # a failed dependency does not make y ready


def test_projections_are_disposable(conn) -> None:  # type: ignore[no-untyped-def]
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p4", "kawa", "rebuild")
    k.derive_work("w", "p4", "implement")
    k.record_result("w", "success", "r-w")
    before = _snapshot(conn)
    n = rebuild(conn)                                    # DROP-equivalent + replay
    assert n >= 3
    assert _snapshot(conn) == before                     # identical after rebuild


def test_execution_unknown_does_not_satisfy_or_proceed(conn) -> None:  # type: ignore[no-untyped-def]
    """#53 §10 / Phase-1 exit: an `execution_unknown` Result must NOT satisfy a dependency or let
    downstream Work proceed — unknown is not success, so a consequential effect cannot be blindly
    retried or treated as done. Verification is required first."""
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("pu", "kawa", "execution-unknown safety")
    k.derive_work("act", "pu", "implement", role_requirement="Implementer")   # a consequential effect
    k.derive_work("next", "pu", "implement", role_requirement="Implementer")
    k.declare_dependency("next", "act", "ALL")

    # the effect's outcome is UNKNOWN (crash-after-effect-before-Result), not success
    k.record_result("act", "execution_unknown", "r-act-unknown")

    with conn.cursor() as cur:
        cur.execute("SELECT dependency_state FROM current_work_dependency "
                    "WHERE work_ref='next' AND dependency_work_ref='act'")
        dep_state = cur.fetchone()[0]
        cur.execute("SELECT execution FROM current_work WHERE work_ref='act'")
        act_exec = cur.fetchone()[0]
        cur.execute("SELECT execution FROM current_work WHERE work_ref='next'")
        next_exec = cur.fetchone()[0]

    assert dep_state == "pending", f"unknown must NOT satisfy the dependency, got {dep_state}"
    assert act_exec == "execution_unknown", f"the effect Work stays execution_unknown, got {act_exec}"
    assert next_exec != "ready", f"downstream must NOT become actionable on unknown, got {next_exec}"


def test_agent_replacement_resumes_from_kawa_state(conn) -> None:  # type: ignore[no-untyped-def]
    """#72 Phase-2 exit: agent replacement / runtime loss does not erase organizational continuity.
    A fresh runtime holding NO prior context resumes the workflow purely from durable Kawa state via
    work.next — no agent-to-agent messaging, no in-runtime queue. Kawa holds the work; runtimes pass
    through."""
    # Runtime A sets up the workflow, then disappears (it held no workflow state — all durable in Kawa).
    a = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="runtime-a", actor_ref="agent-a"))
    a.create_plan("pr", "kawa", "resume-continuity")
    a.derive_work("build", "pr", "implement", role_requirement="Implementer")
    a.derive_work("check", "pr", "review", role_requirement="Reviewer")
    a.declare_dependency("check", "build", "ALL")
    del a  # runtime A gone

    # Runtime B — brand new, no prior context — discovers the actionable Work from durable state.
    b = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="runtime-b", actor_ref="agent-b"))
    nxt = b.work_next("Implementer")
    assert nxt is not None and nxt["work"]["work_ref"] == "build"     # resumed purely from Kawa state
    b.record_result("build", "success", "r-build")
    del b  # runtime B gone mid-workflow

    # Runtime C — also new — picks up the now-unblocked downstream Work. Result, not a message, routed it.
    c = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="runtime-c", actor_ref="agent-c"))
    nxt = c.work_next("Reviewer")
    assert nxt is not None and nxt["work"]["work_ref"] == "check"     # continuity survived two runtime losses
    c.record_result("check", "success", "r-check")
    assert c.work_state("check") == "finished"


def test_console_renders_from_live_projections(conn) -> None:  # type: ignore[no-untyped-def]
    """Phase 3: the Console renders from LIVE projections, never a static snapshot — a change is
    reflected on the next render."""
    from kawa.console.render import render_page
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("pc", "kawa", "console")
    k.derive_work("wc", "pc", "implement", role_requirement="Implementer")
    page1 = render_page(conn)
    assert "pc" in page1 and "wc" in page1 and "READY" in page1   # live current_work.execution='ready'
    k.record_result("wc", "success", "r-wc")                     # change the live state
    page2 = render_page(conn)
    assert "finished" in page2 and page2 != page1                # re-read reflects it (no snapshot)


def test_console_shows_dispatch_from_live_work_dispatch(conn) -> None:  # type: ignore[no-untyped-def]
    """Phase 3: the Runtime & Dispatch section renders from live work_dispatch (how Work was routed
    to runtimes/lanes)."""
    from kawa.adapters import broker_bridge as bridge
    from kawa.console.render import render_page
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("pd", "kawa", "dispatch")
    k.derive_work("wd", "pd", "review", role_requirement="Reviewer")
    bridge.request_review(conn, work_ref="wd", context="ctx", target_agent="vendor-A", transport="broker")
    from kawa.console.render import render
    page = render(conn, "/dispatch")
    assert page is not None and "vendor-A" in page and "broker" in page


def test_console_shared_sidebar_and_routing(conn) -> None:  # type: ignore[no-untyped-def]
    """The sidebar is shared across pages (generated once from SCREENS); each screen is its own page;
    the active page is highlighted; a planned screen shows an honest placeholder; unknown path = 404."""
    from kawa.console.render import render
    route = render(conn, "/")
    disp = render(conn, "/dispatch")
    for page in (route, disp):                                    # every page carries the same nav
        assert page is not None
        assert 'href="/"' in page and 'href="/dispatch"' in page and 'href="/fleet"' in page
    assert 'class="nav-item active" href="/"' in route            # active highlight differs per page
    assert 'class="nav-item active" href="/dispatch"' in disp
    fleet = render(conn, "/fleet")                                # designed-but-not-implemented
    assert fleet is not None and "not implemented" in fleet and "planned" in route
    assert render(conn, "/nope") is None                          # unknown path -> 404


def test_attested_emit_stamps_provenance(conn, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Phase 4A: an attested IdentityContext binds origin_node to the credential and Emit signs the
    canonical content_hash. Signature is provenance, not identity (event_id stays = self_hash)."""
    from kawa.domain.credential import load_or_create_local_node, verify_provenance
    cred = load_or_create_local_node(str(tmp_path / "node.json"), node_ref="node-att")
    k = Kawa(conn, identity=IdentityContext.from_local_node(cred, actor_ref="agent-att"))
    ev = k.create_plan("pa", "kawa", "attested")
    with conn.cursor() as cur:
        cur.execute("SELECT origin_node, event_id, self_hash, signature, signing_key_ref, "
                    "signature_scheme FROM events WHERE event_id=%s", (ev.event_id,))
        onode, eid, ch, sig, kref, scheme = cur.fetchone()
    assert onode == "node-att"                         # origin bound to the credential, not a caller string
    assert eid == ch                                   # identity is still content_hash, unchanged
    assert sig is not None and kref == cred.signing_key_ref and scheme == "ed25519"
    assert verify_provenance(ch, sig, cred.public_pem()) is True    # cryptographic provenance verifies


def test_observation_policy_digest_rides_the_attest_envelope(conn) -> None:  # type: ignore[no-untyped-def]
    """A policy-bound Observation lands its policy_digest in events.policy_digest (ATTEST:
    'policy in force'), machine-queryable — not only as prose inside source_revision.
    Omitting it stays NULL (no fabricated policy binding)."""
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    dig = "sha256:" + "c" * 64
    ev = k.record_observation("archive_restore_proof", value_bool=True, method="command_exit",
                              source_ref="file:///tmp/segments", content_digest="sha256:" + "d" * 64,
                              fetched_at="2026-08-16T00:00:00Z",
                              source_revision=f"policy_digest={dig}", policy_digest=dig)
    ev2 = k.record_observation("archive_restore_proof", value_bool=True, method="command_exit")
    with conn.cursor() as cur:
        cur.execute("SELECT event_id, policy_digest FROM events WHERE event_id IN (%s, %s)",
                    (ev.event_id, ev2.event_id))
        got = dict(cur.fetchall())
    assert got[ev.event_id] == dig and got[ev2.event_id] is None


def test_unattested_emit_has_null_signature(conn) -> None:  # type: ignore[no-untyped-def]
    """An unattested runtime records a NULL signature honestly — never a faked one."""
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="node-u", actor_ref="a"))
    ev = k.create_plan("pu2", "kawa", "unattested")
    with conn.cursor() as cur:
        cur.execute("SELECT signature, signing_key_ref FROM events WHERE event_id=%s", (ev.event_id,))
        assert cur.fetchone() == (None, None)


def test_context_bootstrap_orients_from_live_state(conn) -> None:  # type: ignore[no-untyped-def]
    """The brief (context.bootstrap) orients a fresh session from live projections: current open plans
    and the next actionable Work — the seamless-handoff mechanism, derived not static."""
    from kawa.brief import bootstrap, render
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("pb", "kawa", "brief target")
    k.set_plan_lifecycle("pb", "running")
    k.derive_work("wb", "pb", "implement", role_requirement="Implementer")
    b = bootstrap(conn)
    assert any(p["plan_ref"] == "pb" for p in b["open_plans"])        # understanding = live open plans
    assert any(w["work_ref"] == "wb" for w in b["next_work"])         # next actionable = live ready Work
    # freshness = the latest EVENT, not the latest plan mutation (a work-level event must move it)
    as_of_after_plan = b["as_of"]
    k.derive_work("wb2", "pb", "implement", role_requirement="Implementer")
    b2 = bootstrap(conn)
    assert b2["as_of"] >= as_of_after_plan and b2["last_event"]["kind"] == "work.derived"
    text = render(b2)
    assert "pb" in text and "wb" in text and "Next actionable Work" in text
    assert "last activity" in text and "work.derived" in text


def test_context_bootstrap_surfaces_stalled_work_and_its_evidence_gap(conn) -> None:  # type: ignore[no-untyped-def]
    """When Work is blocked, the brief names WHAT it waits on — the brief must not fall silent
    exactly when the next move is 'produce the missing evidence'."""
    from kawa.brief import bootstrap, render
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("ps", "kawa", "stall target")
    k.set_plan_lifecycle("ps", "running")
    k.derive_work("dep-a", "ps", "implement")
    k.derive_work("gated", "ps", "implement")
    k.declare_dependency("gated", "dep-a", "ALL")
    b = bootstrap(conn)
    stalled = {w["work_ref"]: w for w in b["stalled"]}
    assert stalled["gated"]["execution"] == "blocked"
    assert [d["work_ref"] for d in stalled["gated"]["waiting_on"]] == ["dep-a"]
    text = render(b)
    assert "Stalled Work" in text and "waiting on: dep-a" in text
    # a RETIRED dependency is a dead branch (#102) — it must not render like an
    # actionable pending/failed one (review 1d352c3c F3)
    k.retire_work("dep-a", "superseded")
    text2 = render(bootstrap(conn))
    assert "dep-a (retired: dead branch — replan)" in text2


def test_brief_vocabulary_covers_every_reducer_execution_state() -> None:
    """Mechanizes review 1d352c3c F1: the stalled section is an explicit whitelist, so a NEW
    execution state added in reducers without a brief-side decision must fail HERE — not
    silently vanish from (or wrongly appear in) the session-start orientation."""
    from kawa.brief import STALLED_STATES, _SETTLED
    from kawa.projections.reducers import _OWN_EXEC
    emitted = set(_OWN_EXEC.values()) | {"ready", "blocked", "retired"}
    known = set(STALLED_STATES) | set(_SETTLED) | {"ready"}
    assert emitted <= known, f"unclassified execution states: {sorted(emitted - known)}"


def test_brief_clip_never_cuts_a_token_in_half() -> None:
    """A blind character cut turns '#122' into '#12' — a phantom ref fed straight into an
    agent's context (review 1d352c3c F4). The clip must land on a whitespace boundary."""
    from kawa.brief import _clip
    text = "vector retrieval, measurement-gated harness for issue #122 and spec section ten"
    clipped = _clip(text, 60)
    assert clipped.endswith("…") and len(clipped) <= 60
    body = clipped[:-1]
    assert text.startswith(body) and (text[len(body)] == " ")        # boundary = whole tokens only
    assert _clip("short", 60) == "short"
    assert _clip("nospaceatall" * 20, 30).endswith("…")              # no-whitespace fallback


def test_context_bootstrap_flags_a_running_plan_with_everything_settled(conn) -> None:  # type: ignore[no-untyped-def]
    """The plan-node-trust limbo (operator request 2026-08-16): a running plan whose every
    Work is finished/retired must be NAMED as having nothing in flight — '[5 finished]'
    alone reads as healthy progress and sat unnoticed until a human audit. A plan with
    live Work must NOT carry the flag."""
    from kawa.brief import bootstrap, render
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p-settled", "kawa", "settled limbo")
    k.set_plan_lifecycle("p-settled", "running")
    k.derive_work("ws1", "p-settled", "implement")
    k.record_result("ws1", "success", "r-ws1")
    k.create_plan("p-live", "kawa", "live plan")
    k.set_plan_lifecycle("p-live", "running")
    k.derive_work("wl1", "p-live", "implement")
    # boundary matrix (review 7a7a9602 F1): retired-only and mixed ARE the limbo;
    # empty, ready, and stalled (retryable/blocked) are NOT
    k.create_plan("p-empty", "kawa", "no work yet")
    k.set_plan_lifecycle("p-empty", "running")
    k.create_plan("p-retired", "kawa", "retired only")
    k.set_plan_lifecycle("p-retired", "running")
    k.derive_work("wr1", "p-retired", "implement")
    k.retire_work("wr1", "superseded")
    k.create_plan("p-mixed", "kawa", "finished plus retired")
    k.set_plan_lifecycle("p-mixed", "running")
    k.derive_work("wm1", "p-mixed", "implement")
    k.record_result("wm1", "success", "r-wm1")
    k.derive_work("wm2", "p-mixed", "implement")
    k.retire_work("wm2", "superseded")
    k.create_plan("p-blocked", "kawa", "stalled work")
    k.set_plan_lifecycle("p-blocked", "running")
    k.derive_work("wb-dep", "p-blocked", "implement")
    k.derive_work("wb-gated", "p-blocked", "implement")
    k.declare_dependency("wb-gated", "wb-dep", "ALL")
    k.record_result("wb-dep", "failure", "r-wb-dep")     # retryable + blocked, zero settled
    b = bootstrap(conn)
    flags = {p["plan_ref"]: p["all_settled"] for p in b["open_plans"]}
    assert flags == {"p-settled": True, "p-retired": True, "p-mixed": True,
                     "p-live": False, "p-empty": False, "p-blocked": False}
    text = render(b)
    settled_line = next(ln for ln in text.splitlines() if "p-settled" in ln)
    live_line = next(ln for ln in text.splitlines() if "p-live" in ln)
    assert "nothing in flight" in settled_line
    assert "nothing in flight" not in live_line


def test_context_bootstrap_collapses_settled_roadmap_steps(conn) -> None:  # type: ignore[no-untyped-def]
    """Settled roadmap steps collapse to counts; only the frontier (un-settled steps) gets lines —
    a wall of 'finished' buries the lines that carry information."""
    from kawa.brief import bootstrap, render
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("plan-roadmap", "kawa", "roadmap")
    k.set_plan_lifecycle("plan-roadmap", "running")
    k.derive_work("w-done", "plan-roadmap", "implement")
    k.record_result("w-done", "success", "r-done")
    k.derive_work("w-open", "plan-roadmap", "implement")
    text = render(bootstrap(conn))
    lines = text.splitlines()
    assert any("Roadmap position (1 finished · 1 ready)" in ln for ln in lines)
    assert any("ready" in ln and "w-open" in ln for ln in lines)
    assert not any("w-done" in ln for ln in lines)                    # settled = collapsed
