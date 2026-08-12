# Kawa MCP Participant Introduction v0.1 (Phase-0 realized)

Status: Draft, current normative — realizes `specification-v0.5.md` §16 as an in-process subset (roadmap step 6, #106)
Companions: `specification-v0.5.md` §16 (+§14/§17), `identity-credential-lifecycle-v0.2.md` (the credential/incarnation this consumes), `postgresql-physical-schema-v0.3.md` (Work rows discovery reads)

> **Require intent. Infer context. Attach trust.** — the LLM states *what* it wants; Kawa attaches *who* it is from the authenticated session.

## 1. Scope (§25): in-process, guarantees named

There is no MCP wire yet. This realizes the participant-introduction **logic** — establishment, reconciliation, session-as-address, capability-gated discovery — in-process; the protocol handshake bytes arrive with step 7 (runtime + a real server). Not new Domain Events (§14): a trust/coordination plane.

| REAL now | DEFERRED (named) |
|---|---|
| `establish_participant` entrypoint; `ParticipantRegistry`; `work_next_for_participant` over the existing `work_next` | the MCP `initialize` bytes/socket (step 7) |
| capability reconciliation (three inputs, typed decisions) | durable **authority policy SoT** — Phase-0 uses a `CapabilityPolicy` fixture; the real source is step-10 authority cells |
| session-as-address registry | persistent Wake reachability from a Runtime/Supervisor (step 7); mediated tool proxies (a `Mediator` seam exists) |

## 2. The four categories (§16)

**Identity (§16.1) — attested, not text.** A `ParticipantSession` is built ONLY from a `verify_credential`-passing `WorkloadCredential` + its `ProcessIncarnation`. No constructor takes a caller-declared identity string; a failing credential yields no session (fail-closed).

**Capability (§16.2) — advertised is not authority.** `reconcile(advertised, mediator, policy)` grants `advertised ∩ mediated ∩ authorized_policy` and records a typed `decision ∈ {granted, not_advertised, not_mediated, not_authorized}` per capability — three orthogonal gates, so an empty authorized set is never an ambiguous silence and policy is never the sole gate. Only `authorized` flows downstream. `mediated` is a real seam (`Mediator = Callable[[set], set]`; the default verifies a configured set, so `not_mediated` is a live path, not decoration).

**Reachability (§16.3) — the session can be the address.** An open session in the `ParticipantRegistry` IS reachability; `drop` makes the participant unreachable. No endpoint string selects a participant — only a `session_id`. Reachability is ephemeral (a restart is a new incarnation, §14.1).

**Introduction (§16.4) — informs routing, never authority.** Attributed self-description (`kind`, `skills`, `specialization`, `operating_constraints`) is routing metadata only. A participant self-describing as `admin` gets no authority from it.

## 3. Discovery: narrower than work_next, never a substitute

`work_next_for_participant(conn, registry, session_id) -> {work?, reason}` is **capability-gated discovery over a currently-reachable, authorized participant** — a strictly narrower view than the global `work_next`. Reachability volatility narrows *discovery*, never Work *availability*: nobody reachable → the participant view is empty, but the Work stays `ready` and the global `work_next` still returns it.

The `reason` is a CLOSED taxonomy so the caller can act on the exact "why nothing":

```text
ok             a Work is returned
unreachable    no live session (unregistered / dropped / restarted)
unauthorized   session live, but not authorized for any ready Work's role
no_ready_work  authorized, but nothing is currently ready
```

**Role and capability are separate axes.** `role_requirement` is the Work-eligibility axis (unchanged since step 4, still OUT of `_recompute_readiness`); capability is the participant-authorization axis. The `role → role:<X>` projection used for the authorization check is an INTERNAL ADAPTER in `work_next_for_participant` only — never a Domain concept, never written. A `ready` Work with no authorized participant stays `ready`.

## 4. Audit / rebuild posture (per artifact)

| Artifact | Home | Rebuild | Why |
|---|---|---|---|
| session / address | in-memory registry | not reconstructed | pure runtime reachability, not an authority grant (§17: presence ≠ a durable claim) |
| advertised capabilities | on the session (ephemeral) | not reconstructed | self-description, never authority |
| authorization (policy) | `CapabilityPolicy` fixture (Phase-0) | n/a — **DEFERRED SoT** | the authority question; not durably owned this step — an honest open gap, step-10 authority cells own it |
| reconciliation result | derived from the three inputs | recomputable | auditable on demand; cannot drift from its inputs |

No new Domain Event kind, envelope column, or migration — each artifact's audit posture is explicit, not assumed.
