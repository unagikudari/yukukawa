"""Step 6 (#106 rev 2) — the negative-proof acceptance + the four round-2 implementation
constraints, as literal tests. Mostly pure; discovery tests use kawa_test_a."""
from __future__ import annotations

import os

import pytest

from kawa.domain.participant import (
    CapabilityPolicy,
    Introduction,
    ParticipantRegistry,
    establish_participant,
    reconcile,
    verifiable_set_mediator,
)
from kawa.domain.workload import CredentialBroker, new_incarnation

_T = ("2026-08-13T00:00:00Z", "2026-08-13T00:05:00Z")
_NOW = "2026-08-13T00:01:00Z"


def _session(advertised, *, verifiable=None, policy_set=None, cred_broker=None,
             introduction=None):  # type: ignore[no-untyped-def]
    broker = cred_broker or CredentialBroker(audience="kawa")
    inc = new_incarnation(node="node-a", runtime="cc", workload="impl")
    cred = broker.request_credential_fn()(inc, capability_ctx=None, iat=_T[0], exp=_T[1])
    mediator = verifiable_set_mediator(set(advertised) if verifiable is None else set(verifiable))
    policy = CapabilityPolicy(authorized=frozenset(advertised if policy_set is None else policy_set))
    return establish_participant(
        credential=cred, incarnation=inc, issuer_pub_pem=broker.issuer_public_pem(),
        expected_issuer=broker.issuer_key_id, expected_audience="kawa", now=_NOW,
        advertised=set(advertised), mediator=mediator, policy=policy, introduction=introduction)


# ---- identity: no session without a verifying credential ----

def test_session_requires_verified_credential() -> None:  # type: ignore[no-untyped-def]
    broker = CredentialBroker(audience="kawa")
    inc = new_incarnation(node="node-a", runtime="cc", workload="impl")
    cred = broker.request_credential_fn()(inc, capability_ctx=None, iat=_T[0], exp=_T[1])
    m = verifiable_set_mediator(set()); pol = CapabilityPolicy(authorized=frozenset())
    # wrong audience -> credential fails -> no session (fail-closed)
    assert establish_participant(credential=cred, incarnation=inc,
        issuer_pub_pem=broker.issuer_public_pem(), expected_issuer=broker.issuer_key_id,
        expected_audience="OTHER", now=_NOW, advertised=set(), mediator=m, policy=pol) is None
    # expired -> no session
    assert establish_participant(credential=cred, incarnation=inc,
        issuer_pub_pem=broker.issuer_public_pem(), expected_issuer=broker.issuer_key_id,
        expected_audience="kawa", now="2026-08-13T01:00:00Z", advertised=set(),
        mediator=m, policy=pol) is None
    # valid -> session established
    assert establish_participant(credential=cred, incarnation=inc,
        issuer_pub_pem=broker.issuer_public_pem(), expected_issuer=broker.issuer_key_id,
        expected_audience="kawa", now=_NOW, advertised=set(), mediator=m, policy=pol) is not None


# ---- §16.5 example + three ORTHOGONAL rejection reasons (round-2 constraints 1,2) ----

def test_reconcile_three_orthogonal_reasons() -> None:  # type: ignore[no-untyped-def]
    # git.read/commit mediated+authorized; git.push authorized but NOT mediated (env can't);
    # tool:x advertised but NOT authorized; role:worker mediated+authorized
    advertised = {"git.read", "git.commit", "git.push", "tool:x", "role:worker"}
    mediator = verifiable_set_mediator({"git.read", "git.commit", "tool:x", "role:worker"})  # push not mediated
    policy = CapabilityPolicy(authorized=frozenset({"git.read", "git.commit", "git.push", "role:worker"}))  # tool:x not authorized
    rec = reconcile(advertised, mediator, policy)
    assert rec.authorized == frozenset({"git.read", "git.commit", "role:worker"})
    assert rec.decisions["git.read"] == "granted"
    assert rec.decisions["git.push"] == "not_mediated"       # authorized but unverifiable -> denied
    assert rec.decisions["tool:x"] == "not_authorized"       # mediated but policy denies
    # all three reasons appear independently — policy is NOT the sole gate
    reasons = set(rec.decisions.values())
    assert {"granted", "not_mediated", "not_authorized"} <= reasons


def test_not_mediated_is_live_not_hollow() -> None:  # type: ignore[no-untyped-def]
    """round-2 constraint 1: a non-identity mediator makes not_mediated a real path — an
    advertised+authorized capability the mediator can't verify is denied."""
    rec = reconcile({"cap.a"}, verifiable_set_mediator(set()),  # verifies nothing
                    CapabilityPolicy(authorized=frozenset({"cap.a"})))
    assert rec.authorized == frozenset()
    assert rec.decisions["cap.a"] == "not_mediated"


def test_reconcile_deterministic() -> None:  # type: ignore[no-untyped-def]
    args = ({"a", "b"}, verifiable_set_mediator({"a"}), CapabilityPolicy(authorized=frozenset({"a"})))
    assert reconcile(*args) == reconcile(*args)


# ---- introduction grants nothing ----

def test_self_description_grants_no_authority() -> None:  # type: ignore[no-untyped-def]
    s = _session([], policy_set={"role:admin"}, introduction=Introduction(kind="admin", skills=("root",)))
    assert s.authorized == frozenset()                       # advertised nothing -> authorized nothing
    assert "role:admin" not in s.authorized                  # self-describing 'admin' grants NOTHING
    assert s.introduction.kind == "admin"                    # kept as routing metadata only


# ---- reachability: session = address; drop -> unreachable; endpoint is not identity ----

def test_registry_session_is_the_address() -> None:  # type: ignore[no-untyped-def]
    reg = ParticipantRegistry()
    s = _session(["role:worker"])
    reg.register(s)
    assert reg.lookup(s.session_id) is s
    reg.drop(s.session_id)
    assert reg.lookup(s.session_id) is None                  # dropped -> unreachable
    assert reg.lookup("not-a-session") is None               # no string substitutes for a session id


# ---- discovery: capability-gated, closed reason taxonomy, readiness untouched ----

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


def test_discovery_gated_by_authorization_readiness_untouched(conn) -> None:  # type: ignore[no-untyped-def]
    from kawa.application.services import Kawa
    from kawa.domain.identity import IdentityContext
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p", "kawa", "discovery")
    k.derive_work("w", "p", "implement", role_requirement="Implementer")
    reg = ParticipantRegistry()

    # a participant authorized for role:Implementer discovers the Work
    auth = _session(["role:Implementer"]); reg.register(auth)
    r = k.work_next_for_participant(reg, auth.session_id)
    assert r["reason"] == "ok" and r["work"]["work"]["work_ref"] == "w"

    # a participant advertising role:Reviewer (authorized) gets it? No — Work needs Implementer.
    reviewer = _session(["role:Reviewer"]); reg.register(reviewer)
    r2 = k.work_next_for_participant(reg, reviewer.session_id)
    assert r2["work"] is None and r2["reason"] == "unauthorized"   # ready Work exists, none for this role

    # readiness is UNCHANGED regardless of who can take it
    assert k.work_state("w") == "ready"
    assert k.work_next("Implementer") is not None                 # global work_next still returns it

    # unreachable: dropped session
    reg.drop(auth.session_id)
    assert k.work_next_for_participant(reg, auth.session_id)["reason"] == "unreachable"

    # re-introduction (a new session) restores discovery
    auth2 = _session(["role:Implementer"]); reg.register(auth2)
    assert k.work_next_for_participant(reg, auth2.session_id)["reason"] == "ok"


def test_discovery_no_ready_work_distinct_from_unauthorized(conn) -> None:  # type: ignore[no-untyped-def]
    from kawa.application.services import Kawa
    from kawa.domain.identity import IdentityContext
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p", "kawa", "empty")                            # no work at all
    reg = ParticipantRegistry()
    s = _session(["role:Implementer"]); reg.register(s)
    r = k.work_next_for_participant(reg, s.session_id)
    assert r["work"] is None and r["reason"] == "no_ready_work"     # authorized, but nothing ready


# ---- advertised-not-authorized never becomes authority (end-to-end) ----

def test_advertised_role_without_authorization_yields_nothing(conn) -> None:  # type: ignore[no-untyped-def]
    from kawa.application.services import Kawa
    from kawa.domain.identity import IdentityContext
    k = Kawa(conn, identity=IdentityContext.from_local_runtime(node_ref="test", actor_ref="pytest"))
    k.create_plan("p", "kawa", "authz")
    k.derive_work("w", "p", "implement", role_requirement="Implementer")
    reg = ParticipantRegistry()
    # advertises role:Implementer but policy does NOT authorize it
    s = _session(["role:Implementer"], policy_set=set())           # authorized nothing
    reg.register(s)
    r = k.work_next_for_participant(reg, s.session_id)
    assert r["work"] is None and r["reason"] == "unauthorized"     # advertise != authority
    assert s.decisions["role:Implementer"] == "not_authorized"


# ---- structural: no Domain footprint ----

def test_no_domain_footprint() -> None:  # type: ignore[no-untyped-def]
    from kawa.domain.events import EventKind
    kinds = {k.value for k in EventKind}
    assert not any("participant" in k or "session" in k or "capability" in k for k in kinds)
