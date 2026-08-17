# Kawa Authority Cells v0.1 — step-10 Phase-0 addendum

Status: Draft, current normative addendum — REALIZED (§25). **Supplements the FROZEN `consistency-and-authority-v0.1.md`; where wording could ever be read to differ, the frozen slab wins.**
Realizes: the slab's Phase-0 subset per roadmap step 10 (issue #118, two-round gate + BC-1..BC-4), three PRs: #119 (taxonomy) → #120 (core+verifier) → this one (CP gates)
Companions: `formal/AuthorityContract.tla` + `formal/check_authority.py` (the machine-checked contract; untouched), `event-taxonomy-v0.2.md` §Authority, `node-identity-and-incarnation-v0.1.md` (fork evidence this gates), `scoped-replication-and-archive-v0.1.md` (the replication receipts ride)

> **Authority persists through a verifiable succession chain; members do not. Unknown is neither false nor authority.**

## 1. Authority is event-sourced

`authority.configuration` (a succession-chain link) and `authority.receipt` (a CP acceptance carrying an accountable quorum proof) are first-class Domain events — replicated, replayed, hash-chained like all history (r1 (a): no non-replicated side channel; D5's prerequisite). They are PROOF material: reducers move nothing; **standing is computed by the three-state verifier at read time** (`kawa/domain/authority.py`, pure). The verifier's chain source is the local event log (`load_proof_store` — materialized events only; a stub is not a configuration you may verify against, BC-3).

## 2. The Phase-0 profile, honestly bounded

- **In-process quorum collection is a proof-collection profile, never a consensus substitute** — no proposal ordering, no liveness claim; the 3-member tests exercise proof arithmetic and verifier semantics only.
- **1-member genesis Cells are the fleet shape**: real machinery (genesis record, receipts, verifier, epochs), degenerate arithmetic (quorum=1). Consequence, stated and tested: **loss or revocation of the sole member's key is catastrophic quorum loss** — every CP operation under that key blocks forever; nothing falls back to operator authority; only a future `RecoveryAnchor` ceremony (slab §7, deferred+named) resumes.
- **Membership is fixed across succession (BC-4)**: a membership-changing succession is structurally INVALID; multi-member Cells are genesis-only until the membership addendum (successor countersign).
- **A Cell member's signing key is PINNED for the Cell's lifetime** (PR #120 finding 3): ordinary rotation removes it from live quorum (rotated ≠ active; BC-4 forbids re-membering the new key) — rotation of a 1-member Cell's key blocks exercise exactly like loss, deliberately and visibly.
- **Conflicts are detected, never resolved** (rev 2 (b)): two facially-valid genesis records for one key — or two facially-valid successors of one parent — block BOTH lines (no last-wins, no second live Cell); the genesis case is recorded durably (`security_authority_conflict`). Unverifiable junk is noise, not a rival (PR #120 blocker 2: a malicious enrolled node cannot grief a Cell with garbage).
- **BC-1 at admission, reconciled with chain continuity**: an unproven genesis event is ADMITTED (dropping a mid-chain event would break its origin's gap-free stream — a worse grief than the one BC-1 prevents) but reported loudly (`authority_genesis_unproven`) and never counted by the verifier. The security outcome BC-1 names is delivered by the facially-valid rule + the conflict record.
- **BC-1 unanimity reading (#164, 2026-08-17)**: the slab's "admissible only under its founding members' signatures" is read as ALL members, not quorum-of-members. Founding is one-shot unanimous; quorum governs operation and succession, never creation — a quorum-only genesis would conscript non-consenting principals into the accountable signer pool (fabricated consent at the membership level, #134's theme). Aligns with the Basin-genesis rule frozen in `basin-federation-invariants-v0.1.md` §3 ("unanimity at genesis is anti-conscription"). This clarifies ambiguous slab wording; where the slab is explicit, the slab wins.

## 3. The CP gates (`kawa/storage/authority_gate.py`)

Commit order is **receipt-first**: the receipt event is durable, replicated history before any operation applies; an operation whose receipt is absent locally is INCOMPLETE and exercises nothing (BC-3). The shared gate judges: held+materialized receipt → **§6.1 policy fence, rule (a), first** (a receipt pinned to a superseded policy is stale however well it binds — re-initiate under the new policy) → operation binding (canonical bytes only; prose is audit metadata, r1 (f)) → three-state verdict at CURRENT trust (S7's forward edge).

- **`resolve_fork` (D1 answered)**: the bare operator path DOES NOT EXIST (structure-tested). BC-2: the operation is consume-once per fork point — idempotent replay under the SAME receipt completes as a no-op; a second resolution of a consumed point is refused (`fork_already_resolved`); rival-chain adoption stays refused (`rival_adoption_deferred`, D2).
- **policy lineage (D6 answered)**: `establish_policy` supersedes the current head through a receipt bound to the exact lineage step (`prior_policy_digest`); `capability_policy_from_lineage` feeds `CapabilityVerifier` from the SoT (the participant fixture remains a test profile, stated). The lineage is append-only; a corrupt multi-head lineage refuses loudly.

## 4. Deferred, named (the D-ledger disposition)

D2 rival-chain adoption (held-tail supersession surgery) · D3 cross-node single-writer/claims · D4 grant governance · D5 trust-change propagation (now mechanical: receipts already replicate) · D7 actuator `CommitToken` (step 12) · `RecoveryAnchor` ceremony · membership-changing succession (successor countersign) · bounded-grace policy fence (rule (b)).

## 5. Realized mapping (§25: implemented, tested)

```text
kawa/domain/events.py          authority.configuration / authority.receipt payloads (#119)
kawa/domain/authority.py       canonical coordinates, facially-valid rules, iterative lineage
                               walks, three-state verify_configuration/current_configuration/
                               verify_receipt (+ trust view) (#120)
kawa/storage/authority_gate.py load_proof_store/load_receipt, the shared CP gate,
                               resolve_fork (receipt-first, BC-2), establish_policy,
                               capability_policy_from_lineage (PR 3)
kawa/storage/replication.py    genesis watch at admission (unproven report + conflict record)
sql/0012, sql/0013             payload tables; policy_lineage, security_authority_conflict
tests/test_authority*.py       S1–S9 + BC-1..4 + three-state + gates end-to-end
formal/check_authority.py      untouched, green — the model is the contract
```

## 6. Deploy order (operational)

Apply `sql/0012` + `sql/0013` before this revision's code (additive; defaults cover all prior rows). A deployment's first authority act is minting its genesis Cells (`authority:fork-resolution`, `authority:policy`) — until then every CP gate refuses, which is the correct zero state.
