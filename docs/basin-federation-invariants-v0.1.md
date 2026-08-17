# Basin Federation and Adversarial Invariants v0.1

Status: **Frozen design decision record** (2026-08-16). Condenses the #159 proposal, its
implementation-grounded falsification, and the owner's adoption decisions. **This document
authorizes no implementation** — each design unit below carries its own tracking issue and
must survive its own adversarial review before code. Where wording could ever be read to
differ from the frozen `consistency-and-authority-v0.1.md` slab, **the slab wins**.

Consolidates: #159 (proposal + 16-comment falsification thread), decisions folded into
#160 (envelope-v3 unit), #161 (decision slots), #162 (witnessing), #163 (proof export),
#164 (Cell-genesis conscription finding). Reviewed against `main` @ 324203b.

Adoption provenance: invariants 1–14 and the Candidate A package carry explicit in-thread
owner adoption; invariants 15–21 and the §13 trigger conditions were Fable-recommended
verdicts adopted by the owner's decision to freeze this document (2026-08-16, out-of-band
of the issue thread).

> **Topology does not imply trust. Membership does not imply authority.
> Topology does not imply disclosure. Time does not create authority.**

---

## 1. Purpose and scope

Kawa must remain correct when multiple Basins coexist in one organization, nodes
legitimately participate in several Basins, and any Basin may be malicious, cloned,
rewound, partitioned, or lost — **without becoming** a BFT consensus system, central
registry, trust-scoring layer, full-mesh replicator, or mandatory-HSM deployment.

The single load-bearing discovery of the falsification round: **spec v0.5 §12 already says
"Events belong to origin lineages" and §13 already separates node identity from continuity
lineage.** The federation design realizes vocabulary the spec froze; the current code is
what collapsed `lineage == node`.

## 2. Adopted invariants

From the #159 proposal (all thirteen adopted; verdicts in §12):

1. Topology does not imply trust.
2. Membership does not imply authority.
3. Multi-basin participation is normal.
4. Confidentiality does not follow from authority integrity.
5. Conflicts are defined over semantic decision slots, not actor-selected IDs — **narrowed, §6**.
6. Authority state/epochs are derived from governance history, never issuer-asserted.
7. Authority is subject to the same provenance/conflict rules as ordinary claims.
8. Search discovers evidence; absence of results never proves absence of evidence.
9. Basin discovery does not make a Basin a search or authority source.
10. Basin identity derives from an immutable consensual genesis statement.
11. Verified history ends at the last verifiable evidence; unknown ≠ nonexistent.
12. Compromise is contained by replaceable instances/credentials; Basin identity survives.
13. Kawa detects contradictions and invalid authority; it never determines intent.

Added during falsification and adopted:

14. **Topology does not imply disclosure.** A principal's participation in multiple Basins
    must not, by itself, cause one Basin to learn another Basin's semantic event metadata.
    Traffic-analysis secrecy (existence/timing/volume) is an explicit non-goal.
15. **Basin creation is permissionless; authority is not.** Genesis establishes Basin
    identity and its initial internal governance anchor, never external legitimacy.
16. **Unanimity at genesis is anti-conscription.** No principal appears in a founding set
    without their signature over the statement. (Cell-layer analog: #164.)
17. **Genesis validity is a function of the artifact alone; the event log is transport.**
18. **Quorum loss must not create authority.** Recovery restores liveness when
    uncontested; it does not grant unilateral truth when contested.
19. **Emergency authority use is an observable fact, not an implementation detail.**
    A consumer must be warned in-band before relying on recovery-derived governance.
20. **Location does not define identity; endpoint discovery is operational state, not
    authority; a relay is a byte carrier, never a trust or authority source.**
21. **Replication is content-centric**: supplier ≠ origin; availability claims are
    scheduling hints; only the verified contiguous prefix advances a lineage frontier;
    origin availability is not required for evidence availability.

## 3. Basin identity and genesis (#160)

- `basin_id = hash(domain_separator ‖ protocol_version ‖ canonical_genesis_statement)`.
  Signatures attest; they never vary the identity. Rival genesis is impossible by
  construction (different statement = different Basin; replay = same Basin, idempotent).
- The genesis statement embeds the **pinned epoch-0 root-Cell coordinate**
  (`authority_key = basin:<derived at bind time>:governance` — the key's basin component
  is bound when `basin_id` is derived, never written literally into the statement, since
  the ID cannot appear inside its own hash preimage; members = declared signer set,
  declared ongoing quorum). Creation requires **all** declared signers (one-shot unanimity);
  operation thereafter uses the declared quorum. A configuration claiming root-Cell
  genesis is facially valid **iff** its digest equals the pinned coordinate — root-Cell
  genesis conflict is thereby impossible-by-construction, removing the enrolled-member
  griefing vector that ordinary authority keys retain (detect-and-block, rev 2 (b)).
- Root governance is an **ordinary authority Cell**: succession, receipts, the
  three-state verifier, and CP gates apply unchanged. No parallel governance subsystem.
- JCS / RFC 8785 canonicalization graduates from replaceable mechanic to prerequisite
  (`ids.canonical_json` is a stated Phase-0 approximation; basin_id makes it load-bearing).
- **Identifier namespaces become basin-local under v3**: `authority_key`, `scope_ref`,
  `node_ref`. The only global identifiers are `basin_id` (content-addressed) and signing
  keys (cryptographic) — both squat-proof. Human names are external recognition claims.
- Stated Phase-0 limitation (F1): under BC-4 + member-key pinning, a 1/1 Basin's founder
  is its permanent governor and key rotation bricks the root Cell. Acceptable for
  dogfood; the **membership addendum** is the dependency for anything more.

## 4. Basin-scoped origin lineage — Candidate A (#160, F4 resolution)

Adopted separation:

```text
node/principal identity   = who signed (accountable, global)
origin_lineage_id         = deterministic ordering coordinate, derive(node_ref, basin_id)
basin_id                  = disclosure / shared-state domain
```

Normative conditions (binding on the v3 design):

1. `origin_lineage_id` lives **inside** the v3 signed preimage, with an explicit
   `domain_separator` — cross-lineage replay is a structural hash mismatch.
2. The lineage coordinate is runtime-derived, never caller-supplied; **incarnation is
   excluded from the coordinate** (including it would let a restore mint a fresh lineage
   at seq 1 and erase positional rollback evidence — reopening the #111 hole).
   Incarnation stays key-lineage in the trust registry; `restore_fork` vs `equivocation`
   classification carries over per lineage unchanged.
3. Lineage admissibility derives from locally-verified basin governance history
   (the Inv-6 pattern), anchored at the basin genesis artifact.
4. Decision-slot identity derives from **authority scope, never lineage/basin** —
   otherwise Sybil basins mint free slots (§6).
5. **Portable misbehavior-proof export (#163) is a dependency, not an enhancement** —
   the SUNDR compensation, see below.
6. Freeze is lineage-local; **distrust is principal-global** (key revocation crosses
   lineages; fork evidence does not).
7. v1/v2 identities are sealed. The existing stream becomes the founding basin's lineage,
   first v3 event chaining onto the v2 head; new basins open lineage genesis at seq 1.

**The honestly-priced trade.** Splitting lineages hands a forking attacker the observer
partition SUNDR requires: positional fork evidence for `(N, B)` is visible only inside
basin B. Quantified: slot-level (semantic) detectability does not degrade (scoped-v2
already withheld the payloads cross-basin); only whole-stream positional visibility is
lost. Compensations: portable proofs (#163, mandatory), lineage-head witnessing (#162),
and an optional governance-decided minimal cross-basin signal ("principal has fork
evidence in some lineage" — seq/existence only, inside the traffic-metadata non-goal).

**Envelope-v2 limitation, stated**: v2 multi-homing shares envelope metadata (`kind`,
`subject_ref`, `actor_ref`, timing, volume) with every basin pulling the origin, and the
hash commitment makes those fields unredactable. Candidate A fixes the cross-basin leak;
the **intra-basin residual** (scope-withheld stubs leak the same fields to non-granted
members of the same basin) survives it — salted field commitments are its only fix and
are reserved v3 option space, not built.

**Rejected alternatives**: per-basin virtual node credentials (Candidate B) — equivocation
would stop being cryptographically same-principal (deniable multi-homing; SSB had to
invent Fusion Identity to repair the same split). Documented as an operational stopgap
only, with the deniability cost stated. Metadata encryption on one shared stream
(Candidate C) — dictionary-trivial for low-entropy fields unless salted, storage
amplification remains; its machinery survives only for the intra-basin residual above.

## 5. Bootstrap

A Basin may be created from nothing: an explicit genesis signer set (1/1 upward),
unanimous over the statement. No dependency on any pre-existing Basin, registry, or
recognition. The genesis-before-lineage circularity dissolves because identity flows from
content, not log position: `basin_id` is computable from the statement alone, the lineage
coordinate follows, and the attestation may ride as seq 1 of any founder's lineage or
arrive out-of-band — equal validity either way (Inv 17). Minimal peer bootstrap material:
`{node_ref, credential public key, one locator}` + the genesis artifact, by any medium.
Neither confers trust; enrollment stays an explicit local act.

## 6. Semantic decision slots — narrowed (#161)

General equivocation detection over arbitrary claims is **not mechanizable** (subject
minting: approve a byte-identical clone Plan P′ while rejecting P; free-text
propositions). The buildable scope: slots attach to **authority-bearing decisions only**,
every coordinate governance-derived — subject from recorded lineage, scope/epoch from the
proven configuration chain, decision kind from the operation vocabulary. Mechanically: a
generalization of the existing `operation_digest` pattern plus a single-slot uniqueness
rule (the `policy_lineage` single-head index is the template). Two validly signed,
mutually exclusive claims by one principal in one slot are equivocation evidence — the
pair is the portable proof. Honest limits (subject minting, free propositions) are stated
in the design, not papered over.

Open dependency (F8): scope grants live in the local, unsigned `TrustRegistry` — so the
"exclusive grant" governance-equivocation class currently has no substrate. Before slots
can cover grants, the design must decide which grants remain local policy and which
become receipt-gated events (the policy-lineage pattern). DEFERRED behind that decision.

## 7. Witnessing (#162)

A witness record is an `ObservationRecorded` with the #98 source-binding tuple — no new
event kind, no Merkle subsystem (possibly one new deterministic method class if
`api_fetch` does not fit honestly). **"Verified-through" semantics**: the witness asserts
"my own admission gate verified this lineage through (seq, hash)" — its admitted
frontier — never "this head was presented to me". The last witnessed head is a verified
**lower bound** (Inv 11): older presented history is detectable rollback; beyond the
witness is unknown, never nonexistent. Witness records carry head hash + seq only.
Merkle/MMR segment proofs remain the §12.3 replaceable mechanic, gated on measurement.

## 8. Recovery from quorum loss

- **R2 — excess redundancy — is REAL today and the default recommendation**: multi-member
  Cells are supported at genesis; `3-of-7` with offline, failure-domain-separated keys
  needs no code. Its fault budget is the honest limit.
- **R1 — pre-declared recovery Cell — DEFERRED into the membership addendum**: an
  ordinary authority Cell (`basin:<id>:recovery`) whose receipt binds
  `(prior_configuration_digest, successor_coordinate)`; the addendum admits a successor
  proven by parent quorum **or** by a VALID recovery receipt. The recovery Cell's key is
  accepted by exactly one gate — successor adoption. Genesis reserves an optional pinned
  recovery-Cell coordinate; later establishment uses the receipt-gated single-head lineage.
- **Activation conditions — recorded recommendation, NOT frozen; slab reconciliation
  pending.** The falsification recommended rejecting any protocol-level "qualifying loss"
  test (proving absence is a failure detector; time/liveness never create authority),
  with safety from **contestability**: a rival successor in the same slot (same
  `prior_configuration_digest`) blocks both lines, retroactively for future exercise,
  while S7 protects already-exercised history — **"uncontested" is always provisional.**
  However, the frozen slab's §7 recovery contract requires **explicit unrecoverability
  evidence**, external trust at least equal to the restored authority, and
  recovery-harder-than-succession — and `authority-cells-v0.1.md` names a future
  `RecoveryAnchor` ceremony as the *only* resume path. Under this document's supremacy
  clause the slab wins as written. The candidate reconciliation, to be settled in the
  membership-addendum review: **R1 is a pre-declared *succession proof path*** (parent
  quorum OR recovery-Cell receipt — ordinary succession under a declared configuration),
  **not slab-§7 recovery** (external re-introduction when no succession proof is
  possible); slab-§7's unrecoverability-evidence MUSTs then continue to govern true
  recovery, untouched. If that reading fails review, the slab's rule stands and the
  contestability model is revised, not the slab.
- **R3 (proactive secret sharing): core REJECT, optional credential profile ACCEPT** —
  a threshold-share-backed `sign()` slots into the `NodeCredential` Protocol seam with
  zero core change; it lowers the probability of quorum loss, never answers it.
- **R5 (auto-shrink / dead-man): REJECT.** R6 (freeze + successor Basin) is the
  zero-mechanism fallback, always available. R4 (external root) is R1 with organizational
  custody — explicit declaration only, never an inferable owner.
- **High finding, stated-window**: stolen recovery credentials grant a usurpation window
  bounded by contest latency. Not protocol-closable; bounded by loud durable receipts,
  the mandatory warning, contest-equals-freeze, and offline custody.
- **Warning is a pure projection** (Inv 19): `governance_continuity` /
  `recovery_from` / `recovery_proof` / `recovery_status` all derive from the
  configuration chain and locally-held rivals; delivered **in the verifier's answer** so
  VALID cannot be obtained without it. Normative wording rule: `uncontested` always means
  "no rival held locally", never "no rival exists".

## 9. Locators and reachability

Already frozen (v0.5 §16.3–16.4) and mechanized in `replication_http`: identity is
challenge + signed request verified against receiver-local registries; the address is an
argument, stored nowhere in the trust plane. Decisions:

- **Signed `NodeEndpointAdvertisement`: REJECT.** It imports replay/freshness/ordering
  problems, and a node-local monotonic locator revision becomes a spurious fork
  coordinate on restore. A successful **authenticated connection is the strongest locator
  observation**; third-party locators are unverified hints; locator state is a bounded,
  rebuildable per-peer cache with local TTL — outside the trust registry.
- Proof-of-reachability: REJECT (the successful connection is the proof, lazily).
- **Two normative confidentiality rules** (the real risk surface — addressing behavior,
  not identity takeover): (i) locator offering is per-peer selection, never globally
  replicated state; private/overlay addresses stay inside their domain; (ii) requests
  name only basins the client can locally verify the peer participates in — asking is
  disclosure. One authenticated session per peer may multiplex basins; the leak lives in
  what is asked, and the signed request digest already covers the ask. Future upgrade
  seam: Willow-style private interest intersection makes the membership-bounded-request
  rule cryptographic instead of behavioral.
- Outbound-only nodes: pull-side works today; serving over an outbound-established
  session is a DEFERRED transport feature. Correctness never depends on it — archive
  segments replicate with zero connectivity through normal admission.

## 10. Content-centric replication (future; sibling of v3)

Already true in `main` and to be preserved: `admit_batch` is supplier-agnostic (supplier
identity has no column and no influence); `archive.py` is origin-free evidence transport
(signed custody commitments, detached segments, offer-to-normal-admission); the frontier
advances only by the verified contiguous prefix. Decisions:

- **Need/fetch layering**: Need is a **local computation** (frontier diff + stub
  backfill), never a wire noun. A peer's served frontier **is** its availability claim —
  hint-grade, verified only by fetching. v1 reconciliation is frontier comparison
  (contiguity makes it an integer per lineage); RBSR is a versioned-format seam for the
  frontier *map* at scale; Rateless IBLT premature; DHT/global index REJECT.
- **Discovery privacy rules**: availability queries are answered only for authenticated
  basin peers, and grade-(ii) relays answer availability to no one outside their
  operating basin (the NDN cache-probing lesson). Stated limitation: lineage coordinates
  are digests, but a **dictionary attack over known (node, basin) pairs de-anonymizes
  them** — the accepted scope-digest boundary, salting is v3-option territory.
- **Eclipse/selective-omission residual (High, stated)**: coordinated holders can starve
  or curate a node's view; witnessing and multi-peer fetch **bound** it, nothing
  eliminates it — the SUNDR limit, and no document may claim otherwise.
- **Staging MUST live outside `events`**: admission is strictly head+1 and
  `frontier()`'s contract is "max() IS the contiguous mark" — out-of-order rows in
  `events` corrupt frontier semantics fleet-wide. Staging is disposable buffer state.
- **Opaque custody has two grades, honestly distinct**: (i) basin-member holder —
  stub-grade envelope verification + a **ciphertext digest committed in the v3 preimage**;
  decrypt = materialize via the existing stub-upgrade byte-verify path. (ii) external
  relay — verifies nothing; carries commitment-attested blobs (the archive-commitment
  shape). "Relay verifies before decryption" is physically unavailable to a true
  outsider without leaking the envelope; stated, not papered over.
- **Key management (DEFERRED design), three constraints fixed now**: no basin-wide key;
  per-scope wrapping to member keys; forward-only rotation on grant revocation
  (materialized plaintext is knowledge — the `revoke_scope` posture).
- Large-blob transport: ADAPT BLAKE3/bao verified streaming (events are already
  self-verifying pieces; no Kawa-specific Merkle format). Cold-archive durability:
  ADAPT Tahoe-style erasure coding over archive segment files — grade-(ii) custody,
  zero semantic impact by insertion point. Hypercore/NDN/Bitswap: reference only.
- **Telemetry split**: per-piece transport telemetry stays local and disposable; only
  digest-grounded anomalies promote to durable Observations ("supplier delivered bytes
  inconsistent with commitment" — contradiction, never motive).
- **Reputation is a local projection**; frozen rule: no mechanism may derive
  trust-registry or scheduling-authority state from replicated observations
  automatically. Local measurements override. Sybil self-rating is inert to trust and
  authority; at most initial-scheduling noise, decaying with contact.
- **Quarantine separation**: local transport quarantine (new, scheduler-local) /
  basin membership removal (governance) / principal revocation (trust registry) /
  lineage freeze (fork machinery) — four mechanisms, never one verb.

## 11. Capability plane and break-glass

Normal Agent DB administration rides `advertised ∩ mediated ∩ authorized_policy`
(participant reconciliation) with the policy SoT in the receipt-gated `policy_lineage` —
application authority never implies infrastructure authority; DB credentials stay behind
the capability boundary. **Break-glass**: the grant is an explicit receipt-gated policy
supersede (durable, replicated, loud — "a human explicitly grants" as signed history);
temporariness rides the workload-credential plane, where wall-clock expiry is already
legitimate precedent (`verify_credential`), distinct from event-history verdicts where
clocks are banned. Anti-silent-permanence: an extraordinary bundle self-declares
`extraordinary: true` + intended expiry, and reconciliation surfaces an in-band warning
on every grant while it is policy head (Inv 19 pattern; overdue = louder). Verified: no
code path widens capability on failure — emergency never *creates* authority.

## 12. Master classification

| Guarantee / mechanism | Verdict |
|---|---|
| Inv 1, 2, 6, 7, 13; 8 (local); 12 (node-level); 4 (honest non-guarantee) | REAL in `main` today |
| Inv 3 (multi-basin participation) | REAL, degenerate — no basin primitive exists to forbid it; realized fully by #160 |
| Inv 9 (discovery ≠ source status) | MOOT until discovery exists; default universe = trust enrollment, inherited not built |
| R3 (proactive secret sharing) | core REJECT; optional `NodeCredential` profile ACCEPT |
| R2 recovery posture; supplier-agnostic admission; origin-free evidence (archive) | REAL today |
| Locator/identity separation; challenge-bound pulls | REAL today |
| Basin genesis + Candidate A (one v3 design unit) | DEFERRED — #160 |
| Decision slots (narrowed) | DEFERRED — #161 |
| Lineage-head witnessing | DEFERRED — #162 (cheapest item) |
| Portable proof export | DEFERRED — #163 (dependency of #160) |
| R1 recovery Cell; recovery-fork adoption path | DEFERRED — membership addendum |
| Recovery/extraordinary-policy warnings | DEFERRED — pure projections, cheap |
| Opaque custody grade (i); ciphertext digest; key mgmt | DEFERRED — v3 + follow-up |
| Swarm scheduler; local quarantine; availability endpoint | DEFERRED — sibling of v3 |
| BFT / registry / trust scores / auto-shrink / dead-man / failure-detector authority | REJECT |
| Signed locator advertisements; proof-of-reachability; DHT; global reputation | REJECT |
| Candidate B (as protocol); Candidate C (as primary F4 fix); Rateless IBLT | REJECT |

## 13. Sequencing and trigger conditions

Binding order: **#145** (future-HLC; before any federation) → **#161** (parallel with
#160 design) → **#160** (basin genesis + Candidate A, one unit: JCS, domain separators,
namespaced identifiers, lineage coordinates, ciphertext-digest reservation) → **#162** →
**#163** (with or before #160's implementation) → **membership addendum** (+ R1 recovery
path — one review unit) → **#147-dependent recovery flows**. Replication/swarming and key
management are siblings consuming v3, not parts of it.

**Implementation triggers** (owner decision, 2026-08-16, out-of-band of the issue
thread) — none of §3–§11's deferred **implementation** starts until at least one trigger
is real: a second Basin with an actual disclosure boundary; an external federation
partner; payload/volume scale that breaks full replication; or publication feedback
demanding it. The **design docs #160–#163 are explicitly exempt** — they were
commissioned by the in-thread decision and may proceed on their own review cadence.
Until a trigger fires, the fleet's implementation priority remains the current roadmap
(durability dogfood, drills) and the live-bug fixes (#145, #147, #149, #164). The
Metafeeds lesson this ordering encodes: land v3 **before** federation ships an install
base — a statement about sequencing relative to federation, not about implementing now.
