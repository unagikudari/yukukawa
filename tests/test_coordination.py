"""Step 7 (#108 rev 2) — the acceptance invariants + the ghost scenarios end-to-end.
Wake/claim are pure in-memory; the discovery tests use kawa_test_a."""
from __future__ import annotations

import os

import pytest

from kawa.domain.coordination import (
    ClaimRegistry,
    WakeBus,
    WakeHint,
)
from kawa.domain.participant import (
    CapabilityPolicy,
    ParticipantRegistry,
    establish_participant,
    verifiable_set_mediator,
)
from kawa.domain.workload import CredentialBroker, new_incarnation

_T = ("2026-08-13T00:00:00Z", "2026-08-13T00:05:00Z")
_NOW = "2026-08-13T00:01:00Z"


def _session(roles=("role:Implementer",)):  # type: ignore[no-untyped-def]
    broker = CredentialBroker(audience="kawa")
    inc = new_incarnation(node="node-a", runtime="cc", workload="impl")
    cred = broker.request_credential_fn()(inc, capability_ctx=None, iat=_T[0], exp=_T[1])
    return establish_participant(
        credential=cred, incarnation=inc, issuer_pub_pem=broker.issuer_public_pem(),
        expected_issuer=broker.issuer_key_id, expected_audience="kawa", now=_NOW,
        advertised=set(roles), mediator=verifiable_set_mediator(set(roles)),
        policy=CapabilityPolicy(authorized=frozenset(roles)))


# ---- wake: hint-only, carries no prose, mutates nothing ----

def test_wake_hint_is_structural_and_carries_no_prose() -> None:  # type: ignore[no-untyped-def]
    h = WakeHint(work_ref="w1", reason="work_eligible")
    assert set(vars(h)) == {"work_ref", "reason"}              # only a ref + typed reason
    # frozen dataclass with two typed fields — there is no field to carry instruction text
    import dataclasses
    assert {f.name for f in dataclasses.fields(WakeHint)} == {"work_ref", "reason"}


def test_wakebus_delivery_is_best_effort_and_stateless() -> None:  # type: ignore[no-untyped-def]
    bus = WakeBus()
    bus.subscribe("s1")
    bus.emit("s1", WakeHint("w1", "work_eligible"))
    bus.emit("s1", WakeHint("w1", "work_eligible"))            # duplicate
    bus.emit("s2", WakeHint("w9", "rescan"))                   # unsubscribed -> dropped
    assert len(bus.drain("s1")) == 2 and bus.drain("s1") == []  # drained, then empty
    assert bus.drain("s2") == []                               # never delivered


# ---- claim: single live holder, lazy expiry, re-claim, release ----

def test_claim_single_live_holder_and_lazy_expiry() -> None:  # type: ignore[no-untyped-def]
    cr = ClaimRegistry()
    assert cr.claim("w", "A", now=100.0, ttl=30.0) is True
    assert cr.holder("w", now=100.0) == "A"
    assert cr.claim("w", "B", now=105.0) is False             # A still live -> B blocked
    assert cr.claim("w", "A", now=110.0) is True              # A re-claims (heartbeat refresh)
    # lease expiry: at/after expires_at the claim is ABSENT
    assert cr.holder("w", now=200.0) is None                  # expired -> absent (lazy pruned)
    assert cr.claim("w", "B", now=200.0) is True              # now B can claim
    assert cr.holder("w", now=200.0) == "B"


def test_release_explicit_and_by_session() -> None:  # type: ignore[no-untyped-def]
    cr = ClaimRegistry()
    cr.claim("w1", "A", now=100.0); cr.claim("w2", "A", now=100.0)
    assert cr.release("w1", "B") is False                     # not the holder
    assert cr.release("w1", "A") is True and cr.holder("w1", 100.0) is None
    freed = cr.release_session("A")                           # observed drop (§17)
    assert freed == ["w2"] and cr.holder("w2", 100.0) is None


# ---- discovery: claim is a DISCOVERABILITY exclusion, never a READINESS change ----

@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    psycopg = pytest.importorskip("psycopg")
    try:
        c = psycopg.connect(os.environ.get("KAWA_TEST_DSN_A", "dbname=kawa_test_a"), autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute("TRUNCATE events, event_plan, event_work, event_work_dependency, event_result, "
                    "event_link, event_observation, event_claim, event_work_retired, event_links, "
                    "current_plans, current_work, current_work_dependency, current_claim_standing, "
                    "runtime_work_occupancy")
    c.commit()
    yield c
    c.close()


def _kawa(conn):  # type: ignore[no-untyped-def]
    from kawa.application.services import Kawa
    from kawa.domain.identity import IdentityContext
    return Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))


def test_claim_excludes_discovery_but_not_readiness(conn) -> None:  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    k.create_plan("p", "kawa", "claim")
    k.derive_work("w", "p", "implement", role_requirement="Implementer")
    reg = ParticipantRegistry(); claims = ClaimRegistry()
    a = _session(); b = _session()
    reg.register(a); reg.register(b)
    # A discovers and claims w
    ra = k.work_next_for_participant(reg, a.session_id, claims=claims, now=100.0)
    assert ra["reason"] == "ok" and ra["work"]["work"]["work_ref"] == "w"
    assert claims.claim("w", a.session_id, now=100.0, ttl=30.0) is True
    # B, a DIFFERENT live participant, does not discover the live-claimed w
    rb = k.work_next_for_participant(reg, b.session_id, claims=claims, now=105.0)
    assert rb["work"] is None and rb["reason"] == "no_ready_work"
    # ...but READINESS is untouched: w stays 'ready' and global work_next returns it
    assert k.work_state("w") == "ready"
    assert k.work_next("Implementer") is not None


# ---- GHOST SCENARIO B: claimed-then-silent -> lease expiry -> B rediscovers, zero operator action ----

def test_ghost_B_silent_holder_lease_expiry(conn) -> None:  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    k.create_plan("p", "kawa", "ghost-B")
    k.derive_work("w", "p", "implement", role_requirement="Implementer")
    reg = ParticipantRegistry(); claims = ClaimRegistry()
    a = _session(); b = _session(); reg.register(a); reg.register(b)

    assert claims.claim("w", a.session_id, now=100.0, ttl=30.0) is True   # A claims
    # A goes SILENT — no drop, no release, no completion
    # before expiry: B cannot get w, and B's claim is refused (single live holder)
    assert k.work_next_for_participant(reg, b.session_id, claims=claims, now=110.0)["work"] is None
    assert claims.claim("w", b.session_id, now=110.0) is False
    # at/after expiry: B pull+claim succeeds — NO operator cancel/re-route
    rb = k.work_next_for_participant(reg, b.session_id, claims=claims, now=200.0)
    assert rb["reason"] == "ok" and rb["work"]["work"]["work_ref"] == "w"
    assert claims.claim("w", b.session_id, now=200.0) is True
    assert claims.holder("w", now=200.0) == b.session_id      # B observable as current holder
    assert k.work_state("w") == "ready"                       # readiness never changed


# ---- GHOST SCENARIO C: session dropped -> immediate release -> B rediscovers ----

def test_ghost_C_session_drop_immediate_release(conn) -> None:  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    k.create_plan("p", "kawa", "ghost-C")
    k.derive_work("w", "p", "implement", role_requirement="Implementer")
    reg = ParticipantRegistry(); claims = ClaimRegistry()
    a = _session(); b = _session(); reg.register(a); reg.register(b)
    assert claims.claim("w", a.session_id, now=100.0, ttl=300.0) is True  # long lease
    # A's session is OBSERVED to drop -> claims released immediately (not waiting for lease)
    reg.drop(a.session_id)
    freed = claims.release_session(a.session_id)
    assert freed == ["w"]
    rb = k.work_next_for_participant(reg, b.session_id, claims=claims, now=101.0)  # well before lease
    assert rb["reason"] == "ok" and rb["work"]["work"]["work_ref"] == "w"
    assert claims.claim("w", b.session_id, now=101.0) is True


# ---- wake never mutates authoritative state (the invariant, plus the anti-injection point) ----

def test_wake_count_does_not_change_pull_result(conn) -> None:  # type: ignore[no-untyped-def]
    k = _kawa(conn)
    k.create_plan("p", "kawa", "wake")
    k.derive_work("w", "p", "implement", role_requirement="Implementer")
    reg = ParticipantRegistry(); claims = ClaimRegistry()
    a = _session(); reg.register(a)
    bus = WakeBus(); bus.subscribe(a.session_id)
    baseline = k.work_next_for_participant(reg, a.session_id, claims=claims, now=100.0)
    for _ in range(5):                                        # many duplicate/stale wakes
        bus.emit(a.session_id, WakeHint("w", "work_eligible"))
    after = k.work_next_for_participant(reg, a.session_id, claims=claims, now=100.0)
    assert after == baseline                                  # wake changed nothing authoritative
    assert k.work_state("w") == "ready"                       # wake created/marked/reordered nothing
    # zero wakes reaches the same authoritative result
    bus2 = WakeBus()  # never emits
    assert k.work_next_for_participant(reg, a.session_id, claims=claims, now=100.0) == baseline


def test_no_domain_footprint() -> None:  # type: ignore[no-untyped-def]
    from kawa.domain.events import EventKind
    kinds = {k.value for k in EventKind}
    # step-7 coordination adds NO Domain event kind (claim.recorded is the step-2 EPISTEMIC
    # Claim, an unrelated concept — the work-claim/lease/wake are pure in-memory coordination)
    assert "wake.emitted" not in kinds and "work.claimed" not in kinds and "lease.granted" not in kinds
    # the full set changes ONLY through a plan gate: step 10 (#118) added the two authority
    # kinds via the vocabulary SoT — coordination still added nothing
    assert kinds == {"plan.created", "plan.lifecycle_changed", "work.derived",
                     "work.dependency_declared", "result.recorded", "link.asserted",
                     "observation.recorded", "claim.recorded", "work.retired",
                     "authority.configuration", "authority.receipt"}
