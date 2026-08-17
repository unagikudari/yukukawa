"""Typed Event vocabulary — the schema-first Domain contract (#57 §5).

The Python types here *implement* the durable contract (sql/0001, the frozen slabs); they do
not define it. Payloads are typed per-kind models, never a `dict[str, Any]` bag (#57 §6).
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from kawa.domain.ids import digest, event_hash


class EventKind(str, Enum):
    PLAN_CREATED = "plan.created"
    PLAN_LIFECYCLE_CHANGED = "plan.lifecycle_changed"
    WORK_DERIVED = "work.derived"
    WORK_DEPENDENCY_DECLARED = "work.dependency_declared"
    RESULT_RECORDED = "result.recorded"
    LINK_ASSERTED = "link.asserted"
    OBSERVATION_RECORDED = "observation.recorded"
    CLAIM_RECORDED = "claim.recorded"
    WORK_RETIRED = "work.retired"
    # step 10 (#118): authority is event-sourced — configurations and Receipts ARE history
    AUTHORITY_CONFIGURATION = "authority.configuration"
    AUTHORITY_RECEIPT = "authority.receipt"


# v0.5 §5 semantic relations + the two coordination relations 0001 carried.
Relation = Literal["supports", "contradicts", "based_on", "reason_for", "addresses",
                   "reviews", "corrects", "resolves", "supersedes", "caused_by", "satisfies"]

# Deterministic collectors only. Model inference is NOT a method class — an LLM's
# interpretation of anything enters as claim.recorded, never as an Observation
# (physical-schema §9; #97 round-1 review point (c)).
ObservationMethod = Literal["command_exit", "http_probe", "file_digest",
                            "api_fetch", "metric_read", "manual_human"]


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)  # fail-loud: no stray fields (#57 §6)


def _drop_unset_step4_keys(d: dict, keys: tuple[str, ...]) -> dict:
    """Set-only dump for fields added AFTER events already existed (#102 round-2 constraint 2):
    payload_digest is computed over model_dump INCLUDING null keys, so a new field that dumped
    `null` would silently change the re-derived digest of every stored event. New keys therefore
    appear only when non-None; pre-step-4 fields keep their exact historical dump shape
    (explicitly writing null into these new fields is not a supported form)."""
    for key in keys:
        if d.get(key) is None:
            d.pop(key, None)
    return d


class PlanCreated(_Payload):
    kind: Literal[EventKind.PLAN_CREATED] = EventKind.PLAN_CREATED
    plan_ref: str
    project_ref: str
    objective: str
    rationale: str | None = None
    lifecycle: Literal["draft", "reviewing", "ready", "running", "blocked", "ended"] = "draft"
    # §6.1 structured intent (step 4, additive). scope is display/summary text — NON-semantic:
    # it enters no identity, dependency, or renderer-basis computation (#102 §6).
    scope: str | None = None
    constraints: list[str] | None = None
    expected_observations: list[str] | None = None       # deliberately not expected_result (§6.1)
    # #156 Phase A (additive): managed domain token — the structural source for
    # principle-aware orientation. Validated fail-closed against the registry at
    # emit; scope above is display-only and is NOT a domain source (r3-F1).
    domain: str | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):  # type: ignore[no-untyped-def]
        return _drop_unset_step4_keys(handler(self), ("scope", "constraints", "expected_observations",
                                                      "domain"))


class PlanLifecycleChanged(_Payload):
    kind: Literal[EventKind.PLAN_LIFECYCLE_CHANGED] = EventKind.PLAN_LIFECYCLE_CHANGED
    plan_ref: str
    lifecycle: Literal["draft", "reviewing", "ready", "running", "blocked", "ended"]
    end_reason: Literal["completed", "cancelled", "failed", "superseded"] | None = None


class WorkDerived(_Payload):
    kind: Literal[EventKind.WORK_DERIVED] = EventKind.WORK_DERIVED
    work_ref: str
    plan_ref: str
    work_kind: str
    role_requirement: str | None = None
    subject_ref: str | None = None
    # §6.2/§8 structured Work contract (step 4, additive; same set-only dump rule)
    objective: str | None = None
    constraints: list[str] | None = None
    expected_observations: list[str] | None = None
    # #156 Phase A (additive): managed domain token, same contract as PlanCreated.domain.
    domain: str | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):  # type: ignore[no-untyped-def]
        return _drop_unset_step4_keys(handler(self), ("objective", "constraints", "expected_observations",
                                                      "domain"))


class WorkRetired(_Payload):
    """#93: the third terminal — an attributed, intentional withdrawal. Neither success nor
    failure; fabricates no Result. Terminal for the Work identity: un-retiring does not
    exist (a plan revision derives a NEW work_ref). Late Results stay recorded but are
    inert for projections (#102 rev 2 precedence rule)."""

    kind: Literal[EventKind.WORK_RETIRED] = EventKind.WORK_RETIRED
    work_ref: str
    reason: Literal["superseded", "cancelled", "obsolete"]
    note: str | None = None


class WorkDependencyDeclared(_Payload):
    kind: Literal[EventKind.WORK_DEPENDENCY_DECLARED] = EventKind.WORK_DEPENDENCY_DECLARED
    work_ref: str
    dependency_work_ref: str
    satisfaction_policy: Literal["ALL", "ANY"] = "ALL"


class ResultRecorded(_Payload):
    kind: Literal[EventKind.RESULT_RECORDED] = EventKind.RESULT_RECORDED
    work_ref: str
    outcome: Literal["success", "failure", "conflicted", "execution_unknown"]
    result_ref: str
    summary: str | None = None
    # #111 8D: the plan-node attempt-lineage occurrence key (occurrence.work_occurrence_key).
    # A payload field, never an envelope/hash-input column; step-4 set-only dump rule so the
    # digests of every pre-step-8 Result are untouched.
    occurrence_key: str | None = None

    @model_serializer(mode="wrap")
    def _ser(self, handler):  # type: ignore[no-untyped-def]
        return _drop_unset_step4_keys(handler(self), ("occurrence_key",))


class LinkAsserted(_Payload):
    """A typed semantic relation as a first-class Event (#97 2A): hash-chained, attributed,
    replication-verified. `event_links` is a projection over these, never written directly."""

    kind: Literal[EventKind.LINK_ASSERTED] = EventKind.LINK_ASSERTED
    source_ref: str
    relation: Relation
    target_ref: str

    @model_validator(mode="after")
    def _no_self_link(self) -> "LinkAsserted":
        if self.source_ref == self.target_ref:
            raise ValueError("self-link is invalid (standing reducer invariant)")
        return self


class ObservationRecorded(_Payload):
    """What a deterministic collector measured. Exactly ONE value type (the §9 gate);
    time fields are canonical ISO-8601 strings so payload_digest stays byte-stable.
    Optional #98 §2 source binding: all-absent, or a coherent snapshot tuple."""

    kind: Literal[EventKind.OBSERVATION_RECORDED] = EventKind.OBSERVATION_RECORDED
    predicate: str
    value_text: str | None = None
    value_number: float | None = None
    value_bool: bool | None = None
    value_time: str | None = None
    observation_method_class: ObservationMethod
    occurred_at: str | None = None
    source_ref: str | None = None
    source_revision: str | None = None
    content_digest: str | None = None
    fetched_at: str | None = None

    @model_validator(mode="after")
    def _gates(self) -> "ObservationRecorded":
        values = [self.value_text, self.value_number, self.value_bool, self.value_time]
        if sum(v is not None for v in values) != 1:
            raise ValueError("exactly one value_* must be set (physical-schema §9 type gate)")
        if self.value_number is not None:
            import math
            if not math.isfinite(self.value_number):
                raise ValueError("value_number must be finite — NaN/inf are not canonical JSON "
                                 "and would poison content addressing (ids.canonical_json)")
        snapshot = (self.source_ref, self.source_revision, self.content_digest, self.fetched_at)
        if any(v is not None for v in snapshot):
            if self.source_ref is None or self.content_digest is None or self.fetched_at is None:
                raise ValueError("source binding requires the coherent tuple "
                                 "source_ref + content_digest + fetched_at (#98 §2)")
        return self


class ClaimRecorded(_Payload):
    """What an accountable actor asserts or infers. Evidence arrives via supports/contradicts
    links — never duplicated into the payload. A Claim need not be true (v0.5 §2.3)."""

    kind: Literal[EventKind.CLAIM_RECORDED] = EventKind.CLAIM_RECORDED
    proposition: str
    basis_note: str | None = None


class AuthorityConfiguration(_Payload):
    """One configuration in an authority key's succession chain (#118 10A; slab §4).

    Genesis (epoch 0) carries `prior_configuration_digest=None` and ALL of its members'
    signatures as the succession proof (BC-1, the #164 unanimity reading: founding is
    one-shot unanimous — nobody is conscripted into an accountable signer pool without
    signing the statement; quorum governs operation/succession, never creation. An
    unsigned or partially-signed genesis is verifier noise — though the admission-time
    conflict watch has a known arrival-order asymmetry, see the #164-review note in
    replication._authority_genesis_watch). A successor
    carries the PARENT quorum's signatures over `configuration_digest`, and Phase-0
    succession must preserve the member set exactly (BC-4 — membership change is a named
    future addendum). Replicated like all history: verifiers fetch the chain, never a
    mutable members table."""

    kind: Literal[EventKind.AUTHORITY_CONFIGURATION] = EventKind.AUTHORITY_CONFIGURATION
    authority_key: str
    configuration_digest: str            # content digest of the canonical config coordinate
    authority_epoch: int
    members: list[str]                   # node signing-key refs — the accountable signer pool
    quorum: int
    prior_configuration_digest: str | None = None
    succession_proof: str | None = None  # canonical-json {signer_set:[key_ref], signatures:[hex]}

    @model_validator(mode="after")
    def _unique_members(self) -> "AuthorityConfiguration":
        # #149 (ADV-05): members is a SET with a quorum rule. Duplicates make _bound's
        # quorum ceiling lie against _valid_signers' distinct-signer count — a
        # [A,A,A]/quorum-3 Cell verifies as bound yet can never exercise (permanent
        # deadlock) and misrepresents its threshold. Rejected at the model, so no
        # such payload can be emitted; verification treats them as non-conforming.
        if len(set(self.members)) != len(self.members):
            raise ValueError("duplicate member refs — a configuration's members are a set")
        return self


class AuthorityReceipt(_Payload):
    """A CP operation's acceptance — a committed Event carrying a quorum proof (slab §5).

    The proof relationships are the contract: statement binding (`operation_digest`,
    canonical bytes only — never free prose), configuration lineage, pinned policy, and an
    ACCOUNTABLE quorum proof (signer set + individual signatures; bare aggregates are
    non-conforming, slab §8). Verification is three-state (VALID/INVALID/INCOMPLETE) and
    lives in the domain verifier — a Receipt event's admission proves provenance, never
    authority standing."""

    kind: Literal[EventKind.AUTHORITY_RECEIPT] = EventKind.AUTHORITY_RECEIPT
    authority_key: str
    operation_digest: str
    configuration_digest: str
    authority_epoch: int
    prior_authority_receipt_digest: str | None = None
    policy_digest: str | None = None
    quorum_proof: str = ""               # canonical-json {signer_set:[key_ref], signatures:[hex]}


Payload = Annotated[
    Union[PlanCreated, PlanLifecycleChanged, WorkDerived, WorkDependencyDeclared, ResultRecorded,
          LinkAsserted, ObservationRecorded, ClaimRecorded, WorkRetired,
          AuthorityConfiguration, AuthorityReceipt],
    Field(discriminator="kind"),
]


class Event(BaseModel):
    """A committed Event envelope. `event_id == self_hash` (content-addressed, chain-linked).

    Step 9a (#113): the envelope is versioned (v2 commits to `scope_digest`), and `payload`
    is nullable — a None payload is a **stub**: an origin-signed envelope whose bytes this
    node does not hold. A stub proves ONLY that the origin committed to `payload_digest` at
    this chain position — never byte possession or content knowledge. Stubs chain, verify,
    and replicate; they are reducer-inert until upgraded (admission byte-verifies on upgrade)."""

    model_config = ConfigDict(frozen=True)

    event_id: str
    origin_node: str
    origin_seq: int
    hlc: str
    kind: EventKind
    subject_ref: str | None
    actor_ref: str
    policy_digest: str | None
    payload_digest: str
    prev_hash: str | None
    self_hash: str
    # step 9a (#113): versioned envelope + scope commitment. scope_ref is v2 cleartext
    # (absent on stubs and unscoped events); scope_digest is what the v2 hash commits to.
    envelope_version: int = 1
    scope_ref: str | None = None
    scope_digest: str | None = None
    # provenance (Phase 4A) — over content_hash (= self_hash); NEVER part of Event identity or verify()
    signature: str | None = None
    signing_key_ref: str | None = None       # which key signed; resolves the historical pubkey (rotation-safe)
    signature_scheme: str | None = None      # mechanics/profile (e.g. 'ed25519'); not a Core Domain enum
    payload: Payload | None

    @property
    def is_stub(self) -> bool:
        return self.payload is None

    def verify(self) -> bool:
        """Re-derive payload_digest and self_hash under the envelope's OWN version; a committed
        Event must be self-consistent. For a stub the committed `payload_digest` is taken as-is
        (the honest limit — bytes are verified at upgrade, not here)."""
        if self.envelope_version not in (1, 2):
            return False                     # unknown version: refuse, never guess
        if self.envelope_version == 1 and (self.scope_ref is not None or self.scope_digest is not None):
            return False                     # structural: a v1 envelope never carries a scope
        if self.scope_ref is not None:
            from kawa.domain.ids import scope_digest_of
            if self.scope_digest != scope_digest_of(self.scope_ref):
                return False                 # cleartext must match the hashed commitment
        pd = digest(self.payload.model_dump(mode="json")) if self.payload is not None else self.payload_digest
        sh = event_hash(
            origin_node=self.origin_node,
            origin_seq=self.origin_seq,
            hlc=self.hlc,
            kind=self.kind.value,
            subject_ref=self.subject_ref,
            actor_ref=self.actor_ref,
            policy_digest=self.policy_digest,
            payload_digest=pd,
            prev_hash=self.prev_hash,
            envelope_version=self.envelope_version,
            scope_digest=self.scope_digest,
        )
        return pd == self.payload_digest and sh == self.self_hash == self.event_id
