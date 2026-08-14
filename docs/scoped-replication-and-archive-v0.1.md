# Kawa Scoped Replication and Archive v0.1 — step-9 Phase-0 addendum

Status: Draft, current normative addendum — realized incrementally (9a REALIZED; 9b, 9c PLANNED per §25)
Realizes: `specification-v0.5.md` §11/§12 per roadmap step 9 (issue #113, two-round plan gate + r2 binding constraints), three PRs: 9a → 9b → 9c
Companions: `event-log-and-replication-v0.1.md` (§1 envelope, §5 scoped replication), `node-identity-and-incarnation-v0.1.md` (step-8 wire/admission/trust plane this builds on)

> **Preserve records. Replicate commitments. Materialize selectively. A hole you can verify is not a gap.**

## 1. Envelope v2 — the scope enters the hash (9a, REALIZED)

Versioned preimages, normative (#113 rev 2 (a)); `ids.event_hash` is the ONLY derivation, used by emit-VERIFY, admission, rebuild replay, and wire verification alike:

```text
v1  H({origin_node, origin_seq, hlc, kind, subject_ref, actor_ref,
       policy_digest, payload_digest, prev_hash})                      -- sealed forever
v2  H(v1 fields + {envelope_version: 2, scope_digest})
    scope_digest = "sha256:" + sha256(scope_ref) | null (unscoped)
```

- The version lives in `events.envelope_version` (DEFAULT 1), the wire envelope, and the model — explicit, never inferred from absence.
- **Downgrade/upgrade forgery is a hash mismatch, not a policy check** (the version is inside the v2 preimage). A v1 envelope carrying any scope is structurally invalid. Unknown versions are refused, never guessed.
- The v2 hash commits to the **digest**, not the raw `scope_ref`: full events carry the cleartext (integrity-checked against the digest); withheld events reveal only the pseudonymous digest. Dictionary attacks on guessable scope names are possible and accepted for Phase-0 (peers are enrolled, credentialed basin nodes); salting is envelope-v3 territory.
- Emit is **opt-in v2** (`scope_ref=` parameter); the default stays v1 — the least-visible default flips in 9b, atomically with the `fleet`-scope grants (r2 BC-iii transition rule).

## 2. Stubs — verifiable holes (9a, REALIZED)

A **stub** is an origin-signed envelope held without its payload bytes (`events.materialized = false`, no payload row). What a stub proves: *"the origin committed to this `payload_digest` at this chain position"* — never byte possession, never content knowledge.

- Stubs chain, verify, advance the head, and replicate onward as stubs; the per-origin gap-free chain survives scope filtering — **a withheld event is a verifiable hole, not a gap**.
- **One materialization predicate** gates reduction in admission AND rebuild (`materialized`): stubs are reducer-inert everywhere; a mixed full/stub log rebuilds to exactly the projections of the full events alone.
- **Upgrade (r2 BC-ii):** re-delivery of a held stub WITH payload routes to the upgrade path, never the idempotent no-op (which would drop the bytes forever — the frontier already counts the stub, so anti-entropy would never re-request it; the frontier is an **envelope-level cursor** by definition). Upgrade byte-verifies against the held commitment, then materializes atomically (payload row + `materialized=true` + reduce, one transaction). Mismatched bytes → `upgrade_digest_mismatch`, reported, non-poisoning (the chain at the head is intact). Upgrade changes materialization eligibility only — identity is fixed.
- The append-only guard on `events` narrows for exactly this one transition: `materialized false→true` + `scope_ref NULL→value` with every hash-relevant column bit-identical (enforced in the trigger). Everything else, and every DELETE, stays forbidden — a node only ever learns bytes it had already committed to by hash.
- Step-9a boundary rule (`serve_batch`): **every v2 event is withheld cross-node** — there are no scope grants until 9b, and least-visible is the fail-direction. v1 events ship full to enrolled-active peers, the legacy carve-out.

## 3. Scope grants and the offer/retain algebra (9b, PLANNED)

Per #113 rev 2 (c): trust-plane `grant_scope`/`revoke_scope`; sender grant permits OFFERING, receiver request+grant permits RETAINING/MATERIALIZING; every disagreement degrades to a stub, never a gap, never a chain break. New reasons `scope_unrequested` (payload dropped, envelope admitted as stub) and `scope_digest_mismatch` (full reject). v2 default becomes least-visible; `fleet` becomes an explicit scope granted at enrollment; the dogfood emitters flip atomically with those grants. Frontier/pull responses name no scope identifiers beyond shared ones.

## 4. Segment commitments and archive (9c, PLANNED)

Per #113 rev 2 (d): a segment commitment is a **verification-and-custody attestation** — archiver A, at time T, under policy P, held and verified contiguous `(origin, from_seq..to_seq)` with boundary hashes and the event-set digest. Not a re-proof of authorship. Detached segments live OUTSIDE the live frontier (archive evidence; they enter the store only as chained gap-fill through normal admission). `scripts/archive_verify.py` re-verifies and records `archive_restore_ok/failed` Observations (source-binding tuple); staleness surfaces in the console, gates nothing (no GC exists; retention policy is a future step). No pruning path exists in step 9.

## 5. Deploy order (operational)

Apply `sql/0010_envelope_v2.sql` BEFORE running the 9a code (emit writes the new columns unconditionally; admission reads `materialized`). Migration-before-code is safe: columns are additive with defaults that describe every pre-step-9 row exactly (`envelope_version=1`, `materialized=true`).

## 6. Realized mapping (§25: implemented, tested — 9a)

```text
kawa/domain/ids.py            versioned event_hash (single derivation) + scope_digest_of
kawa/domain/events.py         Event.envelope_version/scope_ref/scope_digest, nullable payload
                              (is_stub), verify() version dispatch + structural guards
kawa/storage/emit.py          scope_ref opt-in -> v2 stamp + new columns
kawa/storage/wire.py          versioned wire verify; stub representation (payload_canonical null,
                              scope_ref stripped); downgrade guards on the wire
kawa/storage/replication.py   stub admission (envelope-only, head-advancing, reducer-inert),
                              BC-ii upgrade routing on re-delivery, withhold/serve_batch,
                              read_stream serves held stubs as stubs
kawa/projections/reducers.py  load_events/rebuild share the materialized predicate
sql/0010_envelope_v2.sql      columns + v1-scopeless CHECK + narrowed append-only trigger
tests/test_envelope_v2.py     downgrade triple-negative, single-derivation structure test,
                              withheld-as-stub chain survival, mixed rebuild convergence,
                              forged-upgrade refusal + atomic/idempotent upgrade, wire stubs
```

## 7. Status discipline (§25)

REALIZED+TESTED: §1, §2 (9a). DESIGNED/PLANNED: §3 (9b), §4 (9c) — present here so the three PRs share one normative home; each flips to REALIZED as its PR lands. DEFERRED, named: scope-name salting (envelope v3), payload field-level redaction, metadata side channels (volume/timing), grant governance (step 10), retention/pruning policy.
