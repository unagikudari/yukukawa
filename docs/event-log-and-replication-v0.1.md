# Kawa Event Log and Peer Replication v0.1

Status: Draft, normative candidate — the core data structure and how peers replicate it
Revision note (2026-08-12, roadmap step 0): `event_id` unified with `content_hash` (was UUIDv7/ULID) — closes the deviation `postgresql-physical-schema-v0.3` §"naming map" carried as deferred (#71); the implemented envelope (sql/0001, Phases 0–4C) is the realized form.
Scope: The shape of a durable Event, the per-node log, and the replication model that lets nodes join and leave without reconfiguring the fleet.
Supersedes (mechanism): full-mesh PostgreSQL logical replication as the propagation mechanism.
Companions: `emit-enforcement-contract-v0.1.md` (the write that produces these rows), `postgresql-physical-schema-v0.3.md` (local storage), `event-taxonomy-v0.2.md`.

> Replicate Events. Rebuild understanding.

## 0. Why this exists (the problem being poured out)

The reference system replicated at the **table** level, full-mesh: every node published and subscribed to every other node. That is `N × (N−1)` subscriptions — an `O(N²)` topology — and **every node join or leave forced a publication/subscription reconfiguration on every other node**, with copy/ordering barriers that do not compose. It is the single largest source of operational pain in the predecessor, and it directly contradicts Kawa's thesis that *nodes are mortal and join/retire is a cheap, first-class transition* (#20).

The fix is not a better mesh. It is to stop replicating tables and replicate the **event log**, which — because Kawa is event-sourced — is an append-only stream and a far simpler thing to move. The database becomes local storage (a replaceable mechanic, spec §24); replication becomes Kawa's own cursor-based protocol.

> **Data structure first: get the Event and the log right, and replication becomes a cursor, not a mesh.**

## 1. The Event envelope — the core data structure

Every durable Event carries this envelope. The payload and semantic links hang off it (`event-taxonomy`, `core-logical-schema`); this document defines only the structural spine that identity, ordering, integrity, and replication depend on.

| Field | Set by | Purpose |
|---|---|---|
| `event_id` | Emit | **Content-addressed identity: `event_id = content_hash`.** Globally unique, collision-*detecting*, and tamper-evident by construction; node-independent. Time ordering is NOT an id property — it comes from `hlc` (§3). (`subject_ref` keeps UUIDv7 where time-sortable minting matters.) |
| `origin_node` | Emit (attested) | The one node that authored this event. An event has exactly one origin, forever. |
| `origin_seq` | Emit | Per-`origin_node`, **gap-free**, strictly increasing. `(origin_node, origin_seq)` is the **replication cursor key** and a second globally-unique natural key. |
| `origin_prev_hash` | Emit | `content_hash` of this origin's previous event. Forms a **per-origin hash chain**. Impl name: `prev_hash`. |
| `content_hash` | Emit | Hash over the canonical envelope+payload; **is** the Event identity (`event_id = content_hash`) and the value `origin_prev_hash` chains on. Integrity, dedup, and collision detection in one coordinate. Impl name: `self_hash`. |
| `hlc` | Emit | Hybrid Logical Clock `(physical, counter)` — the **causal** timestamp (§3). |
| `subject_ref` | caller intent | The semantic subject the event is about (§2). |
| `subject_seq` | Emit | Per-`subject_ref` position **as seen by this origin** (§2.2 — not globally gap-free). |
| `kind` | caller intent | Domain event kind (canonical name; `event_type` is a retired alias). |
| `actor_ref` / `observer_ref` / `workload_ref` | Emit (attested) | Provenance, stamped from the authenticated session (`emit-enforcement §2.2`). |
| `scope_ref` | caller intent / inferred | Project/scope, for **scoped replication** (§5) and authorization. DESIGNED, not yet physical: adding it to the hashed envelope requires envelope versioning (old events' hashes lack it), so it lands with selective materialization (spec-v0.5 §12, roadmap step 9) — until then replication is unscoped. |
| `occurred_at` / `recorded_at` | caller / Emit | Wall-clock, for display and audit only. **Never authoritative for order** (§3). |
| `causation_id` / `correlation_id` | caller / inferred | Causal and workflow links. |
| `payload` / `links` | caller intent | Typed event body and semantic links, appended atomically (`emit-enforcement §2.5`). |

Two structural properties do all the work below:

- **`(origin_node, origin_seq)` is a gap-free per-origin sequence.** Each node's output is therefore a totally-ordered, hole-free stream — trivial to replicate by cursor and to verify for completeness.
- **`origin_prev_hash` chains that stream.** The per-origin hash chain gives tamper-evidence and gap-detection in one structure — and it is the same chain the accountability layer needs for evidence-grade completeness (#22). One structure, two obligations.

## 2. Subject reference and its ordering

### 2.1 What a subject is
A `subject_ref` is an opaque, node-independent identifier for the enduring thing an event is about (a Project, Problem, Plan, Review, Finding — the entities with independent lifecycle, `core-logical-schema §4`). It is minted by Emit when the subject is first created, never chosen by the caller from a guessable space. Subject identity, split, and merge (a Problem reframed into two) are their own bedrock concern, tracked separately; this document depends only on `subject_ref` being stable and node-independent.

### 2.2 Two sequences, deliberately different
- **`origin_seq`** orders one node's whole output — for replication completeness.
- **`subject_seq`** orders events about one subject — for causal reduction.

`origin_seq` is globally gap-free per origin. `subject_seq` is gap-free **only within a single origin's writes to that subject**. It is *not* a global per-subject counter, because that would require cross-node coordination on every subject write — reintroducing the very fleet-wide barrier we are removing.

> **Honest caveat (refines `emit-enforcement §4.1`).** The per-subject serialization lock in the emit contract is **node-local**. Across the mesh, single-writer-per-subject is not guaranteed by that lock. When two nodes write the same subject concurrently, the result is a **causal fork**, surfaced as explicit conflict (never last-write-wins) — which is exactly the spec's stance on disagreement. Global subject order is therefore *causal* (§3), not a dense counter. Whether a given subject *may* be written from more than one node at once, or must have a single current writer, is the consistency-model question (the next slab: per-operation CAP + fleet-authority) and is out of scope here.

## 3. Causal order (Hybrid Logical Clock)

Wall-clock time cannot order a no-master mesh: `occurred_at` is skewable and back-datable, so it can place an effect before its cause. Kawa stamps each event with a **Hybrid Logical Clock** `hlc = (physical, counter)`:

- On Emit: `hlc.physical = max(local_wall_clock, last_hlc.physical)`; `counter = (physical advanced ? 0 : last_hlc.counter + 1)`.
- On receiving a replicated event `e`: advance the local HLC past `e.hlc` before stamping the next local event, so a locally-emitted event that *observed* `e` is guaranteed `hlc >` `e.hlc` — **happens-before is preserved**.

The **deterministic total order** used by cross-subject reducers is:

```text
(hlc.physical, hlc.counter, origin_node)
```

total (no ties — `origin_node` breaks the last one), identical on every node regardless of arrival interleaving, and **causal** (it never orders an effect before its cause). `occurred_at`/`recorded_at` are retained for display and audit but are never inputs to order or authority. This replaces the wall-clock tuple in `emit-enforcement §4.2`, which was deterministic but not causal.

## 4. The per-node log and the replication frontier

Each node stores its own and its replicated events in a local append-only log (PostgreSQL is one storage engine; it is not the replication mechanism). A node's knowledge of the fleet is one small structure:

```text
frontier : { origin_node → highest CONTIGUOUS origin_seq held }
```

The frontier is the complete anti-entropy state. Because each origin stream is gap-free, "highest contiguous" is exact: a node knows precisely what it is missing from any peer.

### 4.1 Anti-entropy pull
Replication is pull-based epidemic (gossip) anti-entropy:

```text
1. node A contacts a peer B (from A's partial peer set, not the whole fleet)
2. A sends its frontier; B replies with events A is missing (origin_seq ranges beyond A's contiguous mark), scope-filtered (§5)
3. A verifies each event: content_hash, origin_prev_hash chains onto what A holds, HLC advances (§3)
4. A applies (append-only INSERT; emit-enforcement §3 permits replicated INSERTs) and advances its frontier
```

- **Fanout, not full mesh.** A talks to a few peers; events propagate epidemically to all interested nodes. Each node maintains `O(fanout)` peer links, not `O(N)` subscriptions.
- **Chain-verified completeness.** A missing event shows as an `origin_prev_hash` that does not chain — a gap is detectable, not silent.
- **Idempotent.** Re-delivery of an event already held (same `event_id`, which *is* the `content_hash`) is a no-op; a *different* event claiming an already-held `(origin_node, origin_seq)` position is a detected collision → halt+alert (`emit-enforcement §4.3`), never a silent drop.

## 5. Scoped (partial) replication

A node need not hold every event. It replicates only the **scopes** (`scope_ref`) it participates in:

- anti-entropy exchanges are filtered to shared scopes, so a node stores and rebuilds understanding only for what it is authorized and needs to see;
- this bounds per-node storage and rebuild cost as the fleet and history grow, and aligns replication with the authorization boundary (a node cannot pull scopes it has no capability for);
- a node joining a new scope simply starts pulling that scope's origin ranges — no global change.

Full replication is the special case where every node shares one scope. Scoped replication is the default for scale and locality.

## 6. Node churn is O(1)

The payoff, and the reason this data structure was chosen:

```text
JOIN   enroll into trust (#20/#21) → learn a few peer addresses → start anti-entropy pull
       → catch up by frontier. No other node reconfigures anything.
RETIRE revoke credentials (#21) → peers stop pulling from it. Its already-gossiped
       events remain immutable and valid; distrust of its past events, if needed, is a
       forward trust-revocation event (#21), not deletion.
```

Contrast the predecessor, where join/leave was a fleet-wide `O(N)` barrier reconfiguration. Here, churn touches only the joining or leaving node's peer list. Mortal nodes with cheap, first-class join/retire — the thesis, made true by the data structure rather than promised by prose.

## 7. What this closes and what it opens

Closes / repositions: the full-mesh replication scaling defect; makes `#20` node lifecycle operationally cheap; supersedes the wall-clock ordering in `emit-enforcement §4.2` with a causal one; provides the per-origin hash chain that `#22` evidence-grade completeness needs.

Opens (named, not hand-waved — the adjacent bedrock):
- **Consistency model per operation class** — which operations tolerate the causal-fork/eventual model above (Observations, most Claims) and which require agreement before commit (fleet-singleton authority, trust revocation). The next slab.
- **Fleet-global authority without hierarchy** — how a no-master mesh agrees on a single outcome (quorum / designated per-concern authority / consensus) for the CP-side operations. Coupled to the above.
- **Subject identity lifecycle** — mint / split / merge of `subject_ref` (§2.1).
- **Log growth** — snapshot / archival of old origin ranges vs full-replay rebuildability.

## 8. Acceptance tests

```text
cursor-catchup       A joining node with an empty frontier pulls from one peer and reaches
                     an identical event set for its scopes — no other node is reconfigured.
gap-detect           Withhold one event in an origin stream; the next event fails to chain
                     (origin_prev_hash mismatch) and the gap is reported, not silently skipped.
causal-order         Emit B on node Y after Y replicated A from node X; every node orders
                     A before B regardless of wall-clock skew or arrival order.
concurrent-fork      Two nodes write the same subject concurrently; every node converges to
                     the same explicit conflict — never a silent last-write-wins.
churn-O(1)           Add and remove a node; assert no configuration change occurs on any
                     other node (only the joiner/leaver's own peer set changes).
scope-isolation      A node without capability for scope S neither pulls nor stores S's events.
```

## 9. Design note

This is the physical foundation the whole substrate stands on, and it was chosen data-structure-first: the Event envelope's `(origin_node, origin_seq)` gap-free stream and per-origin hash chain make replication a cursor diff and completeness a chain check; the HLC makes order causal without a master; scope filtering makes it scale. No PostgreSQL replication feature is load-bearing — the database is storage, and could be replaced without touching §§1–6. That is the difference between building the peer mesh on bedrock and building it on a mesh of database subscriptions that fractures a little more with every node.
