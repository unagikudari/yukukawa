# Memory Broker → Kawa Migration and Coexistence v0.1

Status: Draft, normative migration contract
Scope: Concrete staged migration from an existing multi-node Memory Broker fleet to Kawa without big-bang cutover or indefinite dual authority.

This document uses synthetic Node names only. Production Node mapping is operator-private state.

## 1. Core rule

> **One semantic family, one authority at a time. Observe twice if safe; decide once.**

Migration is performed per semantic family, not by replacing the whole fleet at once.

## 2. Synthetic topology

The procedure assumes an existing five-Node Broker fleet represented as:

```text
nod_a
nod_b
nod_c
nod_d
nod_e
```

One temporary Kawa migration authority is introduced as a logically separate service boundary. It MAY initially run on existing infrastructure, but its Domain Event store and authority decisions MUST remain distinguishable from Broker state.

No production address, hostname, credential, or topology detail belongs in this repository.

## 3. Migration units

Cutover units are semantic families, for example:

```text
Observation
Claim / inferred legacy evidence
Problem
Plan
Review / Finding
Result
```

Fact is not migrated as an authoritative row. It is reconstructed from imported evidence/events and policy.

Work/lease state is coordination state and is not bulk-imported as durable Domain history.

## 4. Pre-cutover inventory — all Nodes

For each Node `nod_a` through `nod_e`, record privately and verify:

```text
running Broker version/commit
schema migration head
replication/publication state
health state
active writers/readers
relevant semantic-family row counts
latest timestamps/frontiers
known degraded or exceptional state
```

Gate:

```text
all five Nodes have an understood state
no unexplained schema divergence
rollback path to current Broker operation is proven
```

If any Node is not understood, migration stops.

## 5. Import mapping

For one selected semantic family:

1. Freeze the mapping specification.
2. Classify each Broker source shape as:
   - deterministic Observation,
   - accountable Claim/inference,
   - typed Domain Event,
   - historical artifact/reference,
   - ambiguous/unmappable.
3. Preserve original Broker provenance/reference.
4. Never translate ambiguous legacy material directly into accepted Fact.
5. Generate Kawa Events idempotently using a deterministic import identity derived from the legacy source reference.

Import replay MUST be side-effect free.

## 6. Per-Node shadow-read rollout

Roll out read comparison one Node at a time:

```text
nod_a -> compare only
nod_b -> compare only
nod_c -> compare only
nod_d -> compare only
nod_e -> compare only
```

At each Node:

1. Broker remains authoritative.
2. Equivalent authorized query is evaluated against Kawa projection.
3. Compare semantic answer, provenance, visibility, lifecycle, and conflict state.
4. Record mismatch as migration Finding; do not silently normalize it away.
5. Continue only after the Node-specific comparison gate passes.

A failure on `nod_c` does not justify proceeding to `nod_d`.

## 7. Shadow-read acceptance gate

For the selected family, all Nodes MUST satisfy predeclared acceptance criteria such as:

```text
no cross-scope disclosure difference
no missing current object above allowed threshold
no silent conflict collapse
all imported provenance traceable
no replay-triggered side effect
projection rebuild reproduces the same Kawa semantic result
```

Exact quantitative tolerances are family-specific and MUST be declared before measurement.

## 8. Controlled dual observation

Dual ingestion is permitted only for naturally repeatable observation paths, such as deterministic collectors, when duplicate external side effects cannot occur.

```text
collector
  ├─ Broker observation path
  └─ Kawa attested Adapter -> observation.recorded
```

Rules:

- Broker and Kawa may both receive the same observation during validation.
- The collector execution occurs once where possible; fan-out happens after collection.
- Kawa provenance is established by the attested Kawa Adapter, not copied from caller text.
- Dual observation does not mean dual decision authority.

## 9. Write-authority cutover — one family

Before cutover:

```text
Human-authorized migration window
current backup/recovery verified
all five Nodes healthy enough for rollback
Kawa security bootstrap/revocation operational
shadow-read gate passed
```

Cutover order:

1. Block creation of new authoritative Broker writes for the selected semantic family through the controlled entry points.
2. Drain or reconcile in-flight writes.
3. Record final Broker frontier.
4. Import/reconcile records through that frontier.
5. Enable Kawa as the sole authoritative producer for the family.
6. Keep Broker read compatibility/adaptation as needed.
7. Observe all five Nodes for defined verification window.

There MUST NOT be an unbounded period in which both Broker and Kawa accept independent authoritative writes for the same family.

## 10. Per-Node post-cutover verification

For each Node `nod_a` through `nod_e`:

```text
verify authorized reads
verify expected compatibility path
verify no legacy writer still produces authoritative rows
verify health/latency/error budget
verify provenance/reference resolution
verify rollback control remains available
```

The family is not considered fleet-cut-over until all Nodes pass.

## 11. Rollback

Rollback triggers include:

```text
security boundary failure
cross-scope disclosure
unexplained semantic divergence
lost authoritative write
non-rebuildable projection
unrecoverable compatibility failure
performance outside declared migration budget
```

Rollback procedure:

1. Stop Kawa authoritative writes for the affected family.
2. Preserve all Kawa Events; never delete history to hide the failed cutover.
3. Determine whether any Kawa-only accepted writes must be transformed back to Broker-compatible input.
4. Re-enable the Broker authoritative writer only after reconciliation prevents duplication/conflict.
5. Verify all five Nodes.
6. Record the failed cutover as a Result/Finding before retry.

Rollback is a controlled authority transfer, not a blind switch flip.

## 12. Family completion

A family may be declared migrated only when:

```text
Kawa is sole authoritative producer
all five Nodes consume the intended path
rollback window completed
no unresolved HIGH/CRITICAL migration Finding
legacy authoritative writer disabled
import/provenance mapping documented
```

Only then may obsolete Broker paths be retired.

## 13. Migration sequence

Prefer low-risk evidence families before high-authority coordination families:

```text
1. deterministic Observation
2. historical Result/evidence references
3. Claim/inferred legacy evidence
4. Problem
5. Plan
6. Review/Finding
7. approval/execution-adjacent integration only after security gates
```

This ordering is a default, not authority to skip family-specific risk review.

## 14. Extend-vs-rewrite prototype gate

Greenfield Kawa remains provisional until a prototype compares both approaches against the same minimum slice.

The comparison MUST implement, not merely describe:

```text
typed Event emit
projection rebuild
scope-before-retrieval authorization
infrastructure-attached Workload identity/provenance
one Observation import path
one Plan/Review lifecycle path
one stale-write conflict
one rollback/recovery exercise
```

Compare:

```text
code/contract complexity
number of legacy paths modified
migration blast radius
security boundary clarity
rebuildability
failure recovery
operator burden
```

Decision rule:

- If extending Broker satisfies the Kawa invariants with lower complexity and lower migration risk, reopen F-006 and prefer extension.
- If extension requires replacing Broker's foundational write/read/replication contracts in place, retain the greenfield Kawa core.

No decision is justified by naming preference or schema aesthetics.

## 15. Production authorization

This architecture document does not itself authorize a production cutover.

Each family cutover requires an explicit operational Plan, independent Review, applicable Human Approval, current inventory, and rollback verification.

## 16. Acceptance tests

```text
A failed nod_c shadow-read gate stops rollout before nod_d.
Ambiguous Broker evidence cannot silently become Fact.
Imported Events cannot replay external side effects.
Dual observation never creates dual decision authority.
A family has only one authoritative writer after cutover.
Rollback preserves Kawa Event history.
All five Nodes are verified before family completion.
The extend-vs-rewrite decision can be reopened by prototype evidence.
```

## 17. Core rule

> **Migrate evidence first, authority last; move one semantic family at a time.**
