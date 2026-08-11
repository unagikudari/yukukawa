# Formal model — Kawa Authority contract

The `③④` consistency/authority slab is gated (issue #38) on a machine-checked model of the
**algorithm-independent Authority contract**: whatever consensus/reconfiguration profile is
later chosen must satisfy these safety properties.

## Files
- `AuthorityContract.tla` / `.cfg` — canonical TLA+ spec + TLC config (the portable artifact).
- `check_authority.py` — a self-contained **stdlib** exhaustive checker over the *same*
  abstract state machine, for environments without a JVM. Run it directly.

## Run
```
python3 formal/check_authority.py          # no dependencies; runs the bounded check now
tlc -config formal/AuthorityContract.cfg formal/AuthorityContract.tla   # needs Java + tla2tools.jar
```

## What it proves
Over a small instance (4 nodes, quorum 3, one authority key, two conflicting ops) it checks,
over **all reachable states**:
- **S2/S4** `AuthorityOnlyUnderLegit` — every accepted operation sits under a legitimate,
  quorum-proven configuration (no leader/clock/bare-signature authority).
- **S1/S3** `NoConflictingAuthority` — no two incomparable configurations both authorize
  conflicting operations for the same key (no split-brain / dual sovereignty).

A **negative control** disables the unique-succession rule and confirms S1 then breaks
(reproducing a concrete C1/C2 split-brain), so a pass is discriminating, not vacuous.

## The finding that shaped the contract
The first run proved that **quorum overlap with the *parent* is not sufficient**: two
successors can each overlap the parent by a quorum yet be mutually incomparable, letting one
parent authorize two divergent authority lines. The contract therefore requires **unique
authoritative succession** — a configuration may be superseded by at most one successor —
which turns #38's S3 from a stated property into an enforced precondition. This is exactly
why the model exists: to make the invariant survive the states a prose contract skips.

Status: TLA+ spec authored; `check_authority.py` runs green here (TLC pending a JVM).
Coverage: **S1–S4** exhaustively (336 states + two discriminating negative controls);
**§6.1 policy-supersession fence** as an interleaving (two-phase initiate/commit × supersede,
exhaustive + negative control); **S5 stale-receipt-after-supersession** as an interleaving
(negative control fires); **S8** (recovery ≠ reconfiguration) and **S9** (independent authority
domains) via targeted checks; **S5/S6/S7** (no stale resurrection, distrust-forward-only,
clocks-cannot-create-authority) by construction and asserted. **C13 / §9 gate-18 below-oracle**
(the hard part): for the chosen v0.1 profile (individual signatures + signer bitmap) the checker
drops the quorum-proof *oracle* and models explicit signers/votes with ≤f Byzantine equivocating
— verifying (i) below-oracle safety (no two conflicting certs with ≤f Byzantine; f+1 reproduces
the conflict), (ii) signer attribution via the bitmap (bare aggregate/threshold leaves
equivocation unattributable — the negative control), and (iii) reconfiguration-under-fault (two
incomparable successors cannot both be certified with ≤f Byzantine parent members).
**gate 13 recovery × delayed-proof** and **gate 12 lease-expiry × clock-skew** are now checked
as interleavings, each with firing negative controls (drop the recovery fence → a delayed
predecessor proof creates a second live lineage; drop the unrecoverability guard → recovery
fires on a live predecessor; unbounded clock skew → unbounded honor-past-expiry window).
**F7/F8 consume-once authority** (`check_consume_once_authority()`, RFC #40/#41): a consume-once
effect (`Actuator.CommitToken`, singular `approval.consume`) is authorized at most once **across
all configurations** — a legitimate successor cannot re-authorize it. The negative control treats
the key as revisable and reproduces the exact cross-config double-authorization `{(C0,A),(C1,B)}`
the generic S1 rule wrongly tolerated — the finding that rescinded a premature freeze.
Remaining before the slab freezes (per the ③④ §9 gate): an independent adversarial re-review of
the consume-once fix (a §10 Core semantic change); the TLC port for larger instances (needs a
JVM); and re-running the below-oracle checks if the profile changes.
