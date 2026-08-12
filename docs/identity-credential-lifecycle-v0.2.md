# Kawa Identity and Credential Lifecycle v0.2 — realized Phase-0 addendum

Status: Draft, current normative addendum to v0.1
Supplements: `identity-credential-lifecycle-v0.1.md` (the design), realizing a Phase-0 subset per roadmap step 5 (#104)
Companions: `specification-v0.5.md` §14–§15, `security-model-v0.1.md`, `#98` (attest intent / bind by digest / separate attestation from execution grant)

> **Name the guarantee, not the component. A security object must never over-promise what Phase-0 verifies.**

## 1. What is REAL in Phase-0 (this step) — and the exact attacker each stops

The security plane is `kawa/domain/workload.py` (+ `kawa/storage/security_store.py`), deliberately OUTSIDE the Domain event log (§14: not every layer is a durable Domain entity).

| Feature | Stops, in-process, NOW | Does NOT stop (deferred / out of model) |
|---|---|---|
| **Process Incarnation** (`new_incarnation`) | conflating a restarted process with its predecessor; PID reuse/aliasing being treated as identity | nothing network-facing |
| **WorkloadCredential** issuer-signed (`CredentialBroker`) | forged / self-issued identity; tampered / expired / wrong-audience / wrong-issuer / wrong-key-namespace credential | a network attacker replaying the token string over a wire (→ transport, step 6/7) |
| **Local capability binding** (`CapabilityVerifier`) | a *copied credential without the matching PoP private key* used **within this process** — cnf.jkt binding + a nonce'd signature + an actual `iat`/`now` freshness check over the operation | transport holder-of-key vs a network MITM; cross-process / restart replay; **malicious code sharing this process, which can read the incarnation's PoP private key directly from memory** |
| **Work Attestation** (`make/verify_attestation`) | a Work whose attested semantics were edited after signing (TOCTOU at the Work level) | authority to *execute* an external effect (→ Execution Grant + adapter, step 7/8) |
| **Key separation** | agent-facing code obtaining issuer signing material | a compromised broker process (out of model) |

**In-process capability binding is NOT HTTP DPoP.** It proves "the caller controls this PoP private key inside this Python process, right now" — nothing about the wire, and nothing against other code in the same address space.

## 2. Deferred, named (never silently skipped)

`DPoP-over-HTTP` proof · `JWKS` distribution endpoint (step 6) · durable / cross-process nonce replay cache · cross-node revocation *propagation* (step 8) · Execution Grant lifecycle + adapter enforcement at a real external effect (step 7/8) · TPM-backed node key (production hardware). The in-process verifier IS the adapter for now; step 7's real transport swaps the capability-binding proof for HTTP DPoP without touching callers.

## 3. Key separation, Python-enforced

The issuer Ed25519 private key is bound only inside the closures created by `CredentialBroker.__init__` (`issue`, `_sign`). Agent-facing code receives `request_credential_fn()` — a plain function that closes over `issue` (a closure over the key) but **never over the broker object and never over a raw key**. It exposes no `sign()` and no key attribute. `WorkloadCredential` is a DISTINCT type from `NodeCredential`, with a `wl-iss:` issuer namespace and `wl-pop:` thumbprint namespace (never the node's `ed25519:`); the two fail each other's verification on both type and namespace. Enforcement is tested by a reachability walk over `__dict__` / `__closure__` / `__self__` / `gc.get_referents`.

## 4. Work Attestation pinned to an immutable snapshot (TOCTOU closed)

The signed payload names a specific `derived_event_id` (an immutable `WorkDerived` event) and `work_semantics_digest` over that event's typed Work fields (§102), plus `source_basis[]` content digests. `verify_attestation` fails unless the caller's recomputed digest — fetched from the event STORE by `event_id`, not the mutable projection — matches. A re-derive of the same `work_ref` produces a new event id and does not match: the attestation is honestly scoped to the version signed. **Signs semantics, never the rendered instruction prose** (#98 §3).

`verify_attestation_against_store` (in `security_store`) is the TOCTOU-closing wiring: it recomputes `work_semantics_digest` by fetching the attested event by id from `event_work` (never the mutable projection) and passes the locally-resolvable `content_digest` set so a vanished `source_basis` source fails the attestation. The pure `verify_attestation` takes both as inputs, keeping the Domain module storage-free.

*Phase-0 constraint (stated):* verification presumes the attested event and its `source_basis` observations remain locally held (append-only, single store, no log pruning). Partial materialization / archival (step 9) and pruning will need a retention or re-fetch policy — noted, not solved here.

## 5. Security-plane storage & survival matrix

`sql/0008` adds `security_credential_issued` (issuance audit), `security_attestation` (kept for later verification), `security_revocation` (local forward-only deny-list). These are append-only, NOT reduced by any reducer, and NOT in `rebuild()`'s TRUNCATE set — a projection rebuild leaves them untouched (tested). They are durable security state, not Domain truth. The **nonce replay cache is intentionally ephemeral**: an in-memory set owned by `CapabilityVerifier`, scoped to one process lifetime — replay is rejected within the process + window; a restart clears it (a restart is a new incarnation anyway, §14.1); durable / cross-process replay protection is deferred.

## 6. Status discipline (§25)

Everything in §1 is IMPLEMENTED and tested in-process; nothing here is DEPLOYABLE as the production profile of v0.1 §15.3. Production requires the deferred §2 items. This addendum exists so the gap is explicit, not discovered later.
