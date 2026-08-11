# Consensus/Reconfiguration Profile Scoring — the last ③④ gate (item 7/18)

Status: Scoring framework (validation, not Core). Feeds the ③④ freeze decision.
Rule: **a profile is selected by scoring against the contract, never by familiarity or implementation convenience** (#38, PR #39 review). A profile is admissible only if it is **conforming (§8): it preserves the S1–S9 Authority invariants and produces verifiable Receipts under its declared fault/synchrony model**. Among conforming profiles, prefer the simplest (Zen: simple > complex, flat > nested).

## What is being scored

The profile is the **replaceable mechanics** (§8) under the fixed Core contract. Three independent axes:

```text
Consensus family     Raft-joint-consensus (CFT) · PBFT · Tendermint · HotStuff ·
                     asynchronous/randomized BFT · DAG-BFT
Reconfiguration      unique-successor/linear · joint-consensus overlap ·
                     dynamic Byzantine lattice/dominance · heterogeneous-quorum
Quorum-proof scheme  individual sigs + signer bitmap · aggregate sig ·
                     threshold Schnorr / FROST · BLS threshold
```

A concrete profile is one choice on each axis. The scoring must cover the cross-axis interactions (e.g. threshold-FROST proof under lattice reconfiguration needs resharing; heterogeneous quorums break simple `3f+1` intuition).

## Scoring criteria (each scored per candidate, with evidence)

```text
C1  fault model            crash-only vs Byzantine; is BFT actually required, or profile/policy-selectable?
C2  synchrony assumptions  synchronous / partial-synchrony / asynchronous; liveness conditions stated
C3  succession safety      guarantees no two incomparable configs confer conflicting authority (S1/S3)
C4  cross-config quorum    quorum intersection across the transition; no dual sovereignty (S3)
C5  Byzantine equivocation tolerated within bound AND detectable/attributable (feeds #21) — NOT opaque
C6  stale-cert invalidation a superseded/forked config's crypto-valid cert cannot confer standing (S5)
C7  Receipt construction   produces a compact, offline-verifiable AuthorityReceipt binding config lineage
C8  signer accountability  compact proof retains individual culpability (accountable-threshold or signer set)
C9  partition behavior     CP transitions fail-closed per authority_key; unrelated keys continue (S9)
C10 quorum-loss / recovery interacts correctly with the RecoveryAnchor; recovery not cheaper than normal (S8)
C11 policy fencing         supports the §6.1 in-flight fence at commit
C12 profile-swap compat    can be replaced by another conforming profile WITHOUT a Core edit (§8, gate 14)
C13 below-oracle proof     the profile model must GENERATE non-equivocating proofs under its Byzantine/
                           partition assumptions — modeled with signers/votes/reachability/cert formation,
                           NOT assumed (gate 18 / review F4). This is where a candidate most often fails.
C14 operational complexity #topology rules, #special-case paths, reconfiguration/resharing cost, recovery
                           complexity, key rotation — fewer moving parts wins ties (Zen)
```

## The below-oracle obligation (the hard part, F4)

The Core model (`AuthorityContract.tla` / `check_authority.py`) treats a valid quorum proof as an **oracle** — it proves the *semantics* preserve the contract *given* a proof. Profile scoring must drop below that line and model, for each candidate, that the profile can actually **produce** valid, non-equivocating proofs under its declared assumptions, exercising: signer/vote sets, correct+Byzantine participants, reachability/partition, certificate construction, cross-configuration intersection/dominance, and **membership change while faults are active**. A candidate that cannot demonstrate this is non-conforming regardless of paper elegance.

## Output

A matrix (candidate × C1–C14) with evidence per cell, a conforming/non-conforming verdict per candidate (conforming = meets §8 + all safety criteria), and — among conforming candidates — a recommendation for the **simplest** one, plus at least one *alternative* conforming profile exhibited (gate 14: proves a swap needs no Core change). No candidate is selected on familiarity; every "conforming" verdict must survive a refute-by-default challenge.

## Process (independence, per the review discipline)

Distinct-vendor lanes score independently; the owner synthesizes from disagreement. The bar is **failure to produce a surviving counterexample** to a "conforming" claim, not reviewer consensus. Candidates that only one lane clears are re-attacked before admission.

---

## Synthesis and owner decision (gate item 7)

Two distinct-vendor lanes scored independently and their raw results are the record:
- **P-A — vendor-A** (consensus family + quorum-proof scheme).
- **P-B — vendor-B** (reconfiguration + operational complexity + below-oracle).

The two lanes **agreed on every safety verdict** and disagreed on exactly one *engineering* axis (the proof scheme). Per the discipline, the synthesis is driven by that single disagreement, and each surviving "conforming" verdict is challenged refute-by-default below.

### Where the lanes agreed (no surviving counterexample from either)

1. **Consensus family.** BFT is **not** Core-mandatory; it is profile/policy-selectable (C1). Any Cell whose declared threat model admits Byzantine peers MUST run a BFT-conforming profile; **Raft/CFT is non-conforming for Byzantine cells** and admissible only for a cell whose policy *explicitly* declares crash-only. Conforming BFT families: **HotStuff, Tendermint, PBFT** (each with explicit, certified reconfiguration). Async/randomized-BFT and DAG-BFT are *conditionally* conforming (must model common-coin / final-certificate construction below oracle) but never minimal. Simplest conforming: **linear-epoch HotStuff/Tendermint**.

2. **Reconfiguration — gate item 17 resolved by refutation.** P-B produced a **surviving refutation of the non-chain (lattice/dominance) profile**: a genuine partial-order reconfiguration ($C_A \parallel C_B$, incomparable) forces the offline verifier to determine the authoritative frontier, which requires either a live oracle / external total-order (breaks 10-year offline verifiability) or recording the join as a strict event-log ordering — which *degenerates to linear unique-successor* anyway. Supporting lattice would leak a vector-clock / frontier-DAG into the Receipt and breach the Core abstraction. **This refutation was not overturned.** Therefore gate 17 resolves to its second branch: **Core commits to comparability-preserving succession** (every legitimate configuration is dominance-comparable to the current authoritative one; no concurrent incomparable authoritative fronts), which is exactly what the formal model's chain-based `Comparable()` already encodes. Conforming realizations: **unique-successor with cross-config quorum overlap** and **joint-consensus ($C_{old} \oplus C_{new}$)**. Both lanes independently confirmed a **direct single-step jump without cross-config overlap is UNSAFE** — and the formal model already forbids it via `OverlapQuorum`.

3. **Reconfiguration-under-active-fault is the decisive case.** Membership change must itself be a **certified CP decision** (`Cell.EpochSuccession`) carrying parent→successor quorum intersection and stale-cert invalidation. Any family that assumes a valid next-membership oracle fails C13.

4. **Bare threshold/aggregate proofs are non-conforming (C8).** A plain FROST/BLS/threshold group signature compresses signers into one group key and **erases individual culpability** — it cannot feed a #21 distrust projection. Both lanes rejected it. Accountability is restored only by an explicit **signer bitmap / participation set** (or an accountable-threshold transcript).

5. **Heterogeneous quorums** (P-B only): conditionally conforming *iff* the cross-config intersection invariant $\forall Q_{old},Q_{new},\forall B\in\mathcal B:(Q_{old}\cap Q_{new})\setminus B \neq \emptyset$ is declared in the `configuration_digest` and is **statically** checkable (the general dynamic case is co-NP-complete / hitting-set). Admissible only as a *tiered, static* profile; not v0.1.

### The one disagreement — quorum-proof scheme — and its resolution

| Lane | Recommendation | Rationale |
|---|---|---|
| P-A (vendor-A) | **Individual sigs + signer bitmap** | simplest (C14), strongest culpability (C8), easiest below-oracle proof (C13), no pairing/DKG; larger receipts |
| P-B (vendor-B) | **Accountable aggregate BLS/MuSig2 + bitmap** | compact $O(1)$ receipt (C7), avoids DKG resharing under reconfiguration |

The disagreement is **not** about safety — both keep the bitmap for accountability and both reject bare threshold. It is receipt-size vs. mechanism-simplicity. The tie-breaker is the Ten-Year offline-verifiability boundary and Zen (simple > complex): **individual signatures (Ed25519-class) verify offline for a decade with ubiquitous, boring crypto and no pairing/DKG lifecycle**; aggregate BLS optimizes bytes at the cost of pairing verification and a heavier key story. So:

- **v0.1 reference proof scheme = individual signatures + signer bitmap.**
- **accountable aggregate (BLS/MuSig2) + bitmap is exhibited as the alternative conforming proof scheme** — which simultaneously **discharges gate item 14** (a profile swap that changes the proof scheme with *no* Core edit, because §8 requires only "verifiable Receipt + attributable signer set", which both satisfy). The disagreement itself produced the required alternative.

### kawa v0.1 reference profile (recommended, refute-survived)

> **Consensus:** BFT linear-epoch (HotStuff- or Tendermint-family) for Byzantine cells.
> **Reconfiguration:** comparability-preserving — unique-successor with cross-config quorum overlap (default), or joint-consensus ($C_{old}\oplus C_{new}$).
> **Proof scheme:** individual signatures + signer bitmap.
>
> **Alternative conforming profiles exhibited (gate 14, no Core edit):** PBFT + explicit reconfiguration certificates; and/or accountable-aggregate BLS/MuSig2 + bitmap as the proof scheme.
> **Non-conforming:** Raft/CFT in any Byzantine cell; bare threshold/FROST/BLS without an accountable signer set; genuine partial-order lattice/dominance reconfiguration.

### Refute-by-default dispositions (owner challenge to each surviving verdict)

- *Split-brain: partition, old config signs successor A, other side signs B.* → Refuted by unique authoritative succession (parent certifies **at most one** successor) + `OverlapQuorum` (the intersection holds ≥1 correct member who will not double-sign). The losing/stale cert reads INVALID/INCOMPLETE. Survives.
- *Stale superseded-config cert replayed.* → Refuted by S5/S6 + the three-state verifier's crypto ≠ lineage ≠ standing separation + `check_stale_receipt()`. Survives.
- *Reconfiguration while faults active.* → Membership change is itself a certified CP decision; direct jump forbidden by `OverlapQuorum`. Survives.
- *CFT downgrade attack: present a Raft/CFT receipt as if it had BFT standing.* → **New hardening required.** The cell's **fault-model declaration must be bound into the content-addressed `policy_digest`**, so an offline verifier can tell a CFT receipt was only ever valid under an explicit crash-only policy and can never be laundered into Byzantine standing. Without this binding, a per-cell CFT allowance is a downgrade surface. Folded into §8 and gate item 21.
- *Individual sigs+bitmap has no safety counterexample*; its only cost (receipt size) is a non-safety C7/C14 trade, covered by the BLS alternative profile. Survives.

**Conclusion:** gate item 7 is satisfied — a concrete profile is scored against the full S1–S9 invariant suite, an alternative conforming profile is exhibited (gate 14), and the lattice branch of gate 17 is closed by a surviving refutation. One new Core-adjacent requirement fell out (fault-model in `policy_digest`, gate 21). The below-oracle obligation (C13 / gate 18) remains the deepest open item: it must be discharged against the *chosen* profile's actual signer/vote/reachability model, not the oracle abstraction.
