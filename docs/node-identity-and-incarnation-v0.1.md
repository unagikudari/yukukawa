# Kawa Node Identity and Incarnation v0.1 — realized Phase-0 addendum

Status: Draft, current normative addendum
Realizes: `specification-v0.5.md` §13 (Node Identity and Incarnation) per roadmap step 8 (issue #111, two-round plan gate + binding constraints BC-1..BC-4)
Companions: `event-log-and-replication-v0.1.md` (the envelope, frontier, anti-entropy), `identity-credential-lifecycle-v0.1/v0.2.md` (key profiles), `operation-effect-identity-v0.8.md` §2 (the occurrence-key keystone this borrows one minter from), `wake-pull-coordination-v0.1.md` (the restart caveat §6 discharges against §5 here)

> **A node's identity is a lineage, not a name. A fork is evidence, never a race to be won.**

## 1. Incarnation is key lineage, not an envelope column

§13 wants `Node Identity / Node Incarnation / origin seq within incarnation / prev hash / signature`. Phase-0 carries the incarnation **through the signing key** (trust registry: `key_ref → (node_ref, incarnation_ref, standing)`) — no hashed envelope change (that requires envelope versioning and lands with step 9's `scope_ref`):

```text
rotate(node, old_key, new_key)                       same incarnation continues — key hygiene.
succeed_incarnation(node, parent_inc, new_inc, key)  NEW incarnation: mandatorily FRESH key
                                                     (reuse would alias two incarnations under one
                                                     attribution key), parentage recorded.
```

The verbs are disjoint by construction, so attributing any authenticated Event to the incarnation that signed it is **total and unambiguous**. Legacy (pre-step-8) enrollments attribute to a deterministic genesis incarnation (`inc:<node>:genesis`).

**"Origin sequence within incarnation" is checkable, not hollowed:** per-incarnation attribution yields per-incarnation `origin_seq` intervals; each incarnation's events must form a contiguous interval, intervals ordered by succession lineage. A violation is itself succession evidence.

## 2. Equivocation and restore-fork (§13: never LWW)

A different authenticated event at an already-held `(origin_node, origin_seq)` is **durable fork evidence** (`security_fork_evidence`, security plane — outside the Domain log, never reduced, survives restart), and the origin **freezes** (every subsequent event refused `origin_frozen`) until an operator resolves it:

```text
same incarnation, same position, two authenticated successors  → equivocation  (Byzantine evidence)
two incarnations, same position                                → restore_fork  (succession fault)
unknown attribution on either side                             → equivocation  (severe end — fail
                                                                 toward scrutiny, never comfort)
```

Unauthenticated junk at a held position stays a plain `collision` — it records no evidence and freezes nothing, or any stranger could freeze a healthy origin (DoS).

**VM clones sharing a key ARE the same incarnation** — their divergence is equivocation, by intent: one incarnation is speaking with two voices. The restore protocol is therefore: a restore/clone that intends to write MUST first `succeed_incarnation` (fresh key + parentage); its divergence then classifies as `restore_fork`.

**Resolution is an explicit, audited operator action** — `resolve_fork(origin, seq, chosen_head, operator_ref, reason)`: forward-only, revokes the losing branch's key **scoped to the fork point** (BC-3: `revoke(key, from_seq)`; late-replicating pre-fork trunk events still verify via `standing(key, at_seq)`), records the resolution on the evidence row, admission resumes. No timeout, no retry count, no arrival order unfreezes anything. Phase-0 can only keep the **held** branch; adopting a rival chain over held history is an authority-level decision (step 10) and is refused, never silently attempted.

## 3. Replication between two real nodes

Two nodes = two DBs + two node credentials + two trust registries + a wire. The transport (`kawa/adapters/replication_http.py`, stdlib) adds reachability, never leniency:

- **Who may pull:** an enrolled, currently-**active** node (Phase-0 read authorization = fleet membership; per-scope authorization is exactly step 9's `scope_ref`). Challenge-response: the client signs `(nonce ‖ frontier ‖ sent_at)` with its node key; the server judges provenance AND standing against **its own** registries (trust is receiver-local in both directions), enforces a freshness window and single-use nonces. Unsigned / unknown / rotated / revoked / stale / replayed / mis-attributed → refusal with no data.
- **Wire canonicalization rule: don't re-canonicalize.** Payloads travel as the origin-canonical JSON text (`canonical_json` — the hashing function); the receiver verifies `payload_digest` and `self_hash` **over the received bytes before parsing** (`storage/wire.py`). Number rendering and Unicode cannot drift on the wire by construction; a byte difference is `wire_invalid`, never a re-interpretation. (Cross-implementation JCS remains ids.py's named debt.)
- Serving is indexed per-origin range queries off the frontier (never a full scan); admission is the **same** trust-gated `admit_batch` as in-process replication — the full rejection matrix holds identically over the wire.
- The nonce cache is in-process per server (step-5 posture): a server restart voids outstanding challenges, failing toward re-challenge — an unknown nonce is refused, never accepted.

## 4. Node churn stays O(1)

Join = enroll (operator trust action on each side that wants to pull/serve) → frontier catch-up over the wire. The serving node reconfigures nothing. Retire = revoke; already-gossiped events remain valid history (distrust is forward-only).

**Deferred, named:** revocation *propagation* between nodes (each operator's registry is a local judgement; propagating trust changes as first-class events is step-10 authority territory) · scoped replication (§5, step 9) · production topology/ops · TPM/HSM node keys.

## 5. The step-7 debt: exactly-once Result recording (8D)

The plan-node occurrence key (the ONE keystone minter realized; registry, drift-CI freeze, client/schedule minters, actuator CommitToken all stay deferred):

```text
occurrence_key = H_OK("plan", plan_ref, work_ref, causal_prior_result_ref)
```

Durable cause bytes only — no node id, log position, wall-clock, or nonce (keystone R1). `causal_prior_result_ref` is the attempt lineage: the Result this attempt retries AFTER (`None` asserts a first attempt), which discriminates the three cases of #111 rev 2 (a): duplicate re-record → same key, contained; crash-before-Result retry → same key, no prior consumer, records; deliberate retry after failure → new key, records.

- **BC-1:** the causal prior comes from the retry **trigger** (dispatch/wake context), never an implicit local query — a lagging replica querying locally would false-merge. `occurrence.work_occurrence_key` takes it as an explicit argument; passing `None` asserts "first attempt", never "locally unknown".
- **BC-2:** a duplicate consumer is admitted to the log (append-only never refuses history) but recorded in `result_occurrence_quarantine` and **inert for projections** — the latest-Result predicate excludes quarantined rows, so no work state, dependency state, or readiness moves. The winner is the FIRST consumer in the causal total order `(hlc, origin)`, recomputed wholesale on each arrival — every node converges to the same winner regardless of arrival order.
- **The still-open window (honest):** external side-effect firing between execution and Result recording. That is the actuator `CommitToken` (spec §20 + keystone), deferred and named — `wake-pull-coordination` §6 states the split.

## 6. Realized mapping (§25: implemented, tested)

```text
kawa/domain/trust.py               incarnation lineage: enroll(..., incarnation_ref),
                                   succeed_incarnation, rotate-preserves-incarnation,
                                   revoke(from_seq)/standing(at_seq) (BC-3)
kawa/storage/replication.py        fork evidence + classification + origin_frozen + resolve_fork;
                                   indexed read_stream ranges
kawa/storage/wire.py               byte-preserving wire, verify-before-parse
kawa/adapters/replication_http.py  challenge-response PullAuthorizer + serve + pull_http
kawa/domain/occurrence.py          H_OK plan-node minter (BC-1 contract)
kawa/projections/reducers.py       occurrence quarantine (BC-2) + quarantine-blind latest-Result
sql/0009_node_incarnation.sql      security_fork_evidence, result_occurrence_quarantine,
                                   event_result.occurrence_key (payload column, never hashed)
tests/test_incarnation.py          8A/8B acceptance incl. negative controls
tests/test_replication_http.py     8C acceptance: auth refusal matrix, wire, 2-node catch-up
tests/test_occurrence.py           8D acceptance: 3-way discriminator, BC-4 replication-lag retry
```

**Deploy order (operational, PR #112 review finding 5):** apply `sql/0009_node_incarnation.sql` (`scripts/apply_migrations.py`) BEFORE running this revision — `emit` writes `event_result.occurrence_key` unconditionally and admission consults `security_fork_evidence`, so code-before-migration fails every `record_result` and every pull. Migration-before-code is safe (the columns/tables are additive and ignored by the previous revision).

## 7. Status discipline (§25)

DESIGNED+IMPLEMENTED+TESTED: everything in §6. DESIGNED only: nothing here. DEFERRED, named: §4 list, actuator gating (§5), envelope `incarnation_ref` column (step 9 envelope versioning), rival-chain adoption (step 10).
