# Kawa Emit Enforcement Contract v0.1

Status: Draft, normative internal correctness contract
Scope: The single durable write path — how `kawa.emit` makes the Core's substrate invariants true by construction rather than by convention.
Closes: spec-v0.2 review blocking findings #6, #8, #9, #10, #11, #12, #13, #14 (8 of 11).
Companions: `postgresql-physical-schema-v0.3.md` (physical realization), `stale-write-guard-v0.1.md` (basis semantics), `reducer-projection-contract-v0.2.md` (order consumption), `subject-identity-and-lineage-v0.1.md` (① — the SERIALIZE key generalizes to `authority_key`; see §2.3), `consistency-and-authority-v0.1.md` (③④ — CP-family authority-domain serialization).

> ゆく川の流れは絶えずして、しかも元の水にあらず。
> The current is durable; the water is never rewritten. This contract is what makes that literally true of the log.

## 0. The insight

The review found eight separate blocking findings — mutable events, deletable links, caller-writable identity, a check-then-act write rule, basis read from a lagging projection, undefined event order, an unbuildable `claimed` state, unenforced collision-freedom. They read as eight problems. They are one.

Each is a place where a substrate invariant was stated in prose and left for "the application to uphold." The spec never named the one place where all of them are decided: **the write.** Kawa already has exactly one public write primitive — `Emit`. What was missing is the statement that Emit is not merely the *preferred* way in, but the *only* way in, and that being the only way in is what gives it the authority to enforce every substrate invariant at a single point.

Naming that point is the fix. This document names it.

> **One durable write path. Every substrate invariant is enforced there, or it is not enforced.**

This is not a band-aid at eight call sites. It is the keystone the arch was drawn without.

## 1. Principle

```text
Emit is the sole path by which a Domain Event becomes durable.
No actor — human, agent, adapter, operator, or replication apply — writes
Domain history except as an append performed or admitted by this contract.
```

"Replaceable mechanics" (spec §24) applies: §§2–5 state the **stable obligations** in mechanism-agnostic terms; §7 gives one **replaceable** PostgreSQL profile. A future storage engine changes §7 and nothing else. If a mechanism cannot meet an obligation in §§2–5, it is not a conforming Kawa substrate — the obligation does not bend to the mechanism.

## 2. The five obligations of Emit

Every Emit MUST discharge these five, in this order, as one indivisible unit. Partial completion leaves no durable trace.

```text
1. IDENTIFY   assign the event's identity and its position in order.
2. ATTEST     stamp provenance from the authenticated context.
3. SERIALIZE  admit at most one writer per subject at the commit point.
4. VERIFY     compare the caller's basis to the durable log frontier.
5. APPEND     write the event and its links atomically, append-only, forever.
```

Zen: *In the face of ambiguity, refuse the temptation to guess.* If any obligation cannot be discharged unambiguously, Emit returns a semantic outcome (`conflict`, `needs_selection`, `precondition_failed`) and writes nothing. It never guesses a value to make the write succeed.

### 2.1 IDENTIFY — the caller never numbers the log
`event_id` and the event's ordering position are assigned **by Emit**, never supplied by the caller. A caller-chosen id is a caller-chosen collision and a caller-chosen order; both are refused inputs, not honored ones. (Closes #14 ordering; supports #12.)

### 2.2 ATTEST — provenance is stamped, never accepted
The attested identity of the write — `origin_node`, `workload_ref`, and, for a collector, `observer_ref` — is derived by Emit from the **authenticated session**, not read from caller-supplied fields. A caller may state *intent* (what to observe, what to claim); it cannot state *who observed* or *which workload wrote*. There is no field on the wire for a caller to declare attested identity, and no code path that copies caller bytes into an identity column. (Closes #9.)

> **The caller requests. The write path attests.** Provenance is established by execution, never claimed by text — and "execution" means this function ran under that session, not that a string said so.

### 2.3 SERIALIZE — one writer per subject at the commit point
At the moment of append, at most one Emit may be in-flight for a given `subject_ref`. This is not advisory ordering; it is a mutual-exclusion obligation the substrate MUST provide (§7 gives the lock). Serialization per subject is what turns obligations 4 and 5 from a hopeful sequence into an atomic one. (Closes #10.)

> **Serialization key — `subject_ref` (AP default) generalizing to `authority_key` (rests on ①/③④).** The key above is `subject_ref`: the AP-family default, and what §7's reference profile locks. For CP-family operations whose authority domain spans subjects, the correct key is the **`authority_key = f(subject_ref, semantic_operation, policy, relevant_lineage)`** defined by `subject-identity-and-lineage-v0.1.md` (①) and serialized within its authority domain per `consistency-and-authority-v0.1.md` (③④) — a `subject_ref`-keyed lock does not serialize a multi-subject authority domain. `subject_seq`'s node-local gap-freeness (§4.1) is unchanged; global per-subject density was never promised. Folding the `authority_key` key into the write path is tracked as **#18**; until then §7 locks on `subject_ref` and CP-family domains rely on ③④'s quorum. This is the note ①/③④ cite emit as resting on.

### 2.4 VERIFY — basis is checked against the log, not the projection
When an Emit is state-dependent (`stale-write-guard-v0.1.md`), its basis MUST be compared to the **durable log frontier for the subject** — the events themselves — read inside the same serialized section as the append (§2.3). Never to a projection, cache, or materialized view, all of which lag. A projection can only ever tell you what *was* true before the reducer fell behind; the log tells you what *is* appended. (Closes #11.)

The frontier is the subject's own append position (§4.1), so VERIFY and APPEND read and write the same monotonic value under the same lock — there is no window between them. Two concurrent revisions of one Plan cannot both observe the pre-write frontier, because the second cannot enter the serialized section until the first has appended and moved the frontier. (This is why #10 and #11 are one lock, not two mechanisms.)

### 2.5 APPEND — atomic, append-only, forever
The event and every semantic link it carries are written in **one transaction** or none. A link is never a later, separate insert — it rides its event's append or it does not exist (a retraction is a *new* event, never a delete). Once appended, no event and no link is ever updated or deleted by any path. (Closes #8, #13's history half, and the durable half of #14.)

## 3. Append-only — the immutability obligation

```text
On the durable Domain tables (events, event_links, typed payload tables):
  INSERT   permitted — from Emit, and from replication apply (§4.3).
  UPDATE   impossible — for every role, every path, forever.
  DELETE   impossible — for every role, every path, forever.
```

The obligation guards **mutation**, never **insertion**. This distinction is load-bearing and hard-won: an append-only guard that also fired on INSERT would reject the rows that logical replication legitimately applies, stalling every downstream node on the first replicated event. Guarding UPDATE/DELETE only makes the guard **replication-safe by construction** — it never sees a legitimate replicated INSERT, so it can never wrongly refuse one. (This is the precise trap the reference system spent a full incident cycle on; it does not get to recur here.)

Immutability is not "the application avoids UPDATE." It is "UPDATE cannot be expressed" — the privilege is not held, and the one privilege that could express it (§7's owner role) runs only insert-shaped code. (Closes #6, and #14's immutability half.)

## 4. Canonical order

Order is not one thing; conflating the two orders is why the spec left it undefined. Kawa needs both, and they are different.

### 4.1 Per-subject order — gap-free, assigned under the lock
Every event carries a `subject_seq`: a per-`subject_ref`, gap-free, strictly increasing position, assigned by Emit as `previous_max + 1` **inside the serialized section** (§2.3). Because it is assigned under the lock, it cannot skip: sequence *N+1* is not handed out until *N* has committed. A stalled writer holds the lock and blocks the next number; it does not let a later number commit ahead of it. This is the direct cure for the "committed 101 while 100 stalls" MVCC gap — the gap is unrepresentable when the counter advances only under mutual exclusion. Per-subject reducers replay by `subject_seq` and need nothing else.

### 4.2 Global order — deterministic total order for cross-subject reduction
Where reduction spans subjects (Fact resolution over Observations and Claims about one subject that arrive as distinct events, federation merge), the canonical total order is:

```text
(occurred_at ASC, origin_node ASC, origin_seq ASC)
```

a lexicographic tuple that is total (no two events tie — `(origin_node, origin_seq)` is globally unique) and identical on every node regardless of arrival interleaving. Clock skew changes *nothing* about the result: two nodes replaying the same event set compute the same order and the same Fact. (Closes #12.)

`occurred_at` is display/causal-intent time and is caller-supplable for historical events; it MUST be ≤ `recorded_at`. It orders, it does not authorize — no basis or approval decision keys on `occurred_at`, only on the log frontier (§2.4).

### 4.3 Replication and identity
`event_id` MUST be collision-free **by construction** — content-addressed: `event_id = content_hash` over the canonical envelope+payload (`event-log-and-replication §1`; 2026-08-12 step-0 revision, was UUIDv7). Identity and integrity are one coordinate: two distinct events cannot share an id without a hash collision. Replication apply INSERTs events directly (§3 permits this). On the vanishing chance of an apply-time id clash:

```text
same id, byte-equal content   → idempotent no-op (safe re-delivery).
same id, differing content     → HALT + alert. A real collision is corruption.
```

Never `ON CONFLICT DO NOTHING`: silently dropping a distinct event is exactly the silent-success-masking class the review names elsewhere. A detected collision is loud. (Closes #14's collision half.)

## 5. `claimed` is not history

`current_work.claimed` cannot be reconstructed from Domain Events because lease/claim state is deliberately *not* a Domain Event (`event-taxonomy-v0.2.md`). Forcing it into the "rebuild from events" set made a definitionally unpassable test.

The resolution is honest separation, not a new event type:

```text
Domain Events        → durable, replicated, rebuild-authoritative.
Coordination state    → durable, node-local, append-only, NOT a rebuild input
                        for Domain semantics; rebuilt from its own store.
```

Work's *semantic* fields (which Problem, which Plan, why) are derived from Domain Events and ARE rebuildable. Work's *coordination* fields (`claimed`, lease, fence) live in the coordination store and are reconstructed from it, never claimed to be replayable from the Domain log. The rebuild acceptance test (physical §20) applies to the semantic projection and explicitly excludes coordination state, which it names as sourced elsewhere. (Closes #13.)

## 6. What this closes

| Finding | Closed by |
|---|---|
| #6 event immutability unenforced | §3 append-only obligation |
| #8 `event_links` mutable non-Event | §2.5 links ride the append; §3 no UPDATE/DELETE |
| #9 identity columns caller-writable | §2.2 ATTEST from session |
| #10 write path check-then-act | §2.3 SERIALIZE |
| #11 basis on a lagging projection | §2.4 VERIFY against log frontier |
| #12 event order undefined | §4.2 deterministic total order |
| #13 `current_work` not replayable | §5 coordination/Domain separation |
| #14 immutability + collision unenforced | §3 + §4.1 + §4.3 |

Not closed here (separate landings, by design — different mechanisms, different documents):

```text
#7  approval fingerprint MUST + canonicalization  → approval-binding revision
#15 review independence undecidable                → F-006 gate revision
#16 comparison record self-declares a winner       → F-006 gate revision
```

## 7. PostgreSQL reference profile (replaceable)

One conforming realization. Replace this section for another engine; §§2–5 do not move.

```text
Roles
  kawa_owner   owns the durable tables. Runs only the emit function body.
  kawa_app     the connected role. Has EXECUTE on emit(), and SELECT.
               Has NO INSERT/UPDATE/DELETE on any durable Domain table.

Privilege
  REVOKE INSERT, UPDATE, DELETE ON events, event_links, <payload tables> FROM kawa_app;
  GRANT  EXECUTE ON FUNCTION kawa_emit(...) TO kawa_app;

Immutability (§3)
  CREATE TRIGGER <t>_append_only BEFORE UPDATE OR DELETE ON <t>
    FOR EACH ROW EXECUTE FUNCTION raise_append_only();   -- RAISE EXCEPTION
  -- BEFORE UPDATE OR DELETE only; INSERT is never guarded (replication-safe).

Emit (§2), SECURITY DEFINER, owned by kawa_owner:
  kawa_emit(event_type, subject_ref, intent_payload, links, basis) →
    PERFORM pg_advisory_xact_lock(hashtextextended(subject_ref, 0));   -- §2.3
    frontier := (SELECT max(subject_seq) FROM events WHERE subject_ref = $);
    IF basis is state-dependent AND basis.frontier <> frontier
       THEN RETURN conflict;                                            -- §2.4
    event_id := content_hash(envelope, payload);                       -- §2.1, §4.3
    INSERT INTO events(event_id, subject_seq := frontier+1,            -- §4.1
                       origin_node := current_node(), workload_ref := auth_workload(),  -- §2.2
                       occurred_at, recorded_at := clock_timestamp(), ...);
    INSERT INTO event_links(...) for each link;                       -- §2.5 same tx
    -- commit is the caller's tx boundary; all-or-nothing.

Identity (§4.3)   event_id = content_hash; UNIQUE(event_id).
                  apply-time same-id re-delivery = no-op; a differing event at an
                  already-held origin position = RAISE (halt+alert), never DO NOTHING.
Order (§4.2)      UNIQUE(origin_node, origin_seq); index on (occurred_at, origin_node, origin_seq).
```

The advisory lock is transaction-scoped: it releases on commit, rollback, or crash — a crashed Emit holds nothing. The same lock discharges SERIALIZE (§2.3), makes VERIFY↔APPEND gapless (§2.4), and makes `subject_seq` gap-free (§4.1): **three findings, one lock.** That economy is the point — the beautiful version of eight fixes is not eight mechanisms.

## 8. Acceptance tests

```text
concurrent-commit    Two Emits on one subject with the same basis: exactly one
                     appends, the other returns conflict. (§2.3/§2.4, #10/#11)
forged-identity      A session emits with a payload naming another workload as
                     observer: the stored observer_ref is the session's, not the
                     payload's. (§2.2, #9)
immutability         UPDATE and DELETE on events/event_links/payload as kawa_app
                     and as kawa_owner both raise. (§3, #6/#8)
replication-insert    A replicated INSERT of a well-formed event is NOT refused by
                     the append-only guard. (§3, regression on the known trap)
gap-free-seq         Interleave a stalled Emit with a later one; no committed
                     subject_seq skips the stalled number. (§4.1, #11 MVCC half)
shuffle-replay       Replay one event set in N random arrival orders; the total
                     order (§4.2) and resulting Fact are identical every time. (§4.2, #12)
collision-halt       Apply two distinct events sharing an id: the second halts and
                     alerts; it is not silently dropped. (§4.3, #14)
rebuild-minus-claim   Delete projections, replay: semantic Work is reconstructed,
                     coordination `claimed` is sourced from the coordination store,
                     and the test does not require replaying it. (§5, #13)
```

Each test is the reproducible closure evidence for its finding — closure the next reviewer runs, not re-argues.

## 9. Design rationale — why this shape lasts ten years

The Zen of Python is the design vocabulary here because Kawa's constitution already speaks it:

```text
There should be one obvious way to do it.   → one durable write path (§1).
Explicit is better than implicit.            → provenance stamped, not inferred (§2.2);
                                               order defined, not assumed (§4).
Special cases aren't special enough to        → replication apply is not a special write
  break the rules.                             door; it INSERTs under the same append-only
                                               law as Emit (§3, §4.3).
Errors should never pass silently.           → a collision halts, a stale write conflicts,
                                               a failed obligation writes nothing (§4.3, §2).
In the face of ambiguity, refuse to guess.   → conflict / needs_selection, never a guessed
                                               value to force success (§2).
If the implementation is hard to explain,     → three findings, one lock (§7); eight findings,
  it's a bad idea.                             one write path (§0). The explanation is short
                                               because the design is one idea, not eight patches.
```

Ten-year test (spec §24): every obligation in §§2–5 is stated without naming PostgreSQL, triggers, advisory locks, or UUIDv7 — those live only in §7 and are labeled replaceable. Replace the engine, the transport, the id library; the five obligations still describe what a durable write must do. That is the difference between a keystone and a band-aid: a band-aid names the wound, a keystone names the load.

## 10. Boundaries

This contract governs the durable **write**. It does not define read authorization (`security-model`), scope resolution (`scope-resolution`), approval binding (`approval-binding`, finding #7), or the F-006 decision gate (findings #15/#16). Those are separate loads with separate keystones. It also assumes, and does not re-specify, that the coordination store (§5) is itself durable and append-only under its own contract.
