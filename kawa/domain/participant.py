"""MCP initialization as participant introduction (v0.5 §16).

The four categories exchanged when a participant joins — Identity, Capability, Reachability,
Introduction — modeled in-process (there is no MCP wire yet; that arrives with step 7's
runtime + a real server). This is a trust/coordination plane, NOT new Domain Events (§14).

The load-bearing invariants:
- **Identity is attested, not text** (§16.1): a session is established ONLY from a
  `verify_credential`-passing WorkloadCredential (step 5) — there is no path that takes a
  caller-declared identity string.
- **Advertised capability is not authority** (§16.2): reconciliation keeps THREE distinct
  inputs — advertised (the participant's request), mediated (what a trusted mediator
  verified), and authorized_policy (the current-authorization source) — and grants only
  `advertised ∩ mediated ∩ authorized_policy`, with a typed reason for every non-grant.
  The policy SoT is DEFERRED (step 10 authority cells); Phase-0 passes a fixture.
- **The session can be the address** (§16.3): an open session in the registry IS the
  reachability fact; dropping it makes the participant unreachable. No endpoint string is
  identity.
- **Introduction informs routing, never authority** (§16.4): self-description is metadata.

Session reachability is ephemeral (a restart is a new incarnation, §14.1). Its volatility
narrows *discovery*, never Work *availability*: nobody reachable → the participant view is
empty, but the Work stays ready and the global `work_next` still returns it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

from kawa.domain.ids import uuid7
from kawa.domain.workload import ProcessIncarnation, WorkloadCredential, verify_credential

# per-capability reconciliation outcome (§16.2) — never a bare present/absent
Decision = Literal["granted", "not_advertised", "not_mediated", "not_authorized"]
# why a participant-scoped discovery returned no Work — a CLOSED taxonomy (round-2 constraint 4)
DiscoveryReason = Literal["ok", "unreachable", "unauthorized", "no_ready_work"]

# A mediator verifies which advertised abilities are actually usable (§16.2 "verified/mediated").
# It is a real seam, not decoration: the default rejects anything outside an explicit
# verifiable set, so `not_mediated` is a live path (a bare identity default would hollow it —
# round-2 constraint 1). Step-7 mediated tool proxies replace this without an API change.
Mediator = Callable[[set[str]], set[str]]


def verifiable_set_mediator(verifiable: set[str]) -> Mediator:
    """A mediator that verifies only the abilities in `verifiable`. Anything advertised but
    outside it is unmediated (its decision becomes `not_mediated`)."""
    frozen = frozenset(verifiable)
    return lambda advertised: {c for c in advertised if c in frozen}


@dataclass(frozen=True)
class CapabilityPolicy:
    """The current-authorization source (§16.2). Its durable SoT is DEFERRED to step 10
    (authority cells); Phase-0 constructs this fixture directly. Swappable behind this type
    with no caller change."""

    authorized: frozenset[str]


@dataclass(frozen=True)
class Reconciliation:
    authorized: frozenset[str]                      # advertised ∩ mediated ∩ policy — the ONLY authority
    decisions: dict[str, Decision]                  # per advertised capability, why granted/denied


def reconcile(advertised: set[str], mediator: Mediator, policy: CapabilityPolicy) -> Reconciliation:
    """Three orthogonal gates, each with an independent reason. A capability is granted only
    if advertised AND mediated AND authorized — and the reason names WHICH gate failed, so an
    empty authorized set is never an ambiguous silence."""
    mediated = mediator(set(advertised))
    decisions: dict[str, Decision] = {}
    authorized: set[str] = set()
    for cap in advertised:
        if cap not in mediated:
            decisions[cap] = "not_mediated"
        elif cap not in policy.authorized:
            decisions[cap] = "not_authorized"
        else:
            decisions[cap] = "granted"
            authorized.add(cap)
    # a capability in policy that was never advertised is simply not requested here
    return Reconciliation(authorized=frozenset(authorized), decisions=decisions)


@dataclass(frozen=True)
class Introduction:
    """Attributed self-description (§16.4). Informs routing preference ONLY; grants nothing."""

    kind: str | None = None
    skills: tuple[str, ...] = ()
    specialization: str | None = None
    operating_constraints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParticipantSession:
    session_id: str
    workload_ref: str
    runtime_ref: str
    process_incarnation_ref: str
    node: str
    logical_agent_ref: str | None                   # descriptive metadata only
    authorized: frozenset[str]                       # the reconciled authority — the only gate
    advertised: frozenset[str]                       # telemetry (self-claim), never authority
    decisions: dict[str, Decision]
    introduction: Introduction = field(default_factory=Introduction)


def establish_participant(*, credential: WorkloadCredential, incarnation: ProcessIncarnation,
                          issuer_pub_pem: str, expected_issuer: str, expected_audience: str,
                          now: str, advertised: set[str], mediator: Mediator,
                          policy: CapabilityPolicy, logical_agent_ref: str | None = None,
                          introduction: Introduction | None = None,
                          revoked: set[str] | None = None) -> ParticipantSession | None:
    """The in-process entrypoint a future MCP `initialize` will call. Establishes a session
    ONLY from a credential that passes `verify_credential` — identity is attested, not
    declared. Returns None (fail-closed) if the credential does not verify."""
    if not verify_credential(credential, issuer_pub_pem=issuer_pub_pem,
                             expected_issuer=expected_issuer, expected_audience=expected_audience,
                             now=now, revoked=revoked):
        return None
    rec = reconcile(advertised, mediator, policy)
    return ParticipantSession(
        session_id=str(uuid7()), workload_ref=credential.payload["sub"],
        runtime_ref=incarnation.runtime, process_incarnation_ref=incarnation.incarnation_id,
        node=incarnation.node, logical_agent_ref=logical_agent_ref,
        authorized=rec.authorized, advertised=frozenset(advertised), decisions=rec.decisions,
        introduction=introduction or Introduction(),
    )


class ParticipantRegistry:
    """Session-as-address (§16.3). An open session IS reachability; drop = unreachable. No
    endpoint string can select a participant — only a session_id. Ephemeral: not
    reconstructed by rebuild (pure runtime reachability, not an authority grant)."""

    def __init__(self) -> None:
        self._sessions: dict[str, ParticipantSession] = {}

    def register(self, session: ParticipantSession) -> None:
        self._sessions[session.session_id] = session

    def lookup(self, session_id: str) -> ParticipantSession | None:
        return self._sessions.get(session_id)

    def drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
