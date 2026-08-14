# Kawa Scoped Replication and Archive v0.1 — step-9 Phase-0 addendum

Status: Draft, current normative addendum — 9a, 9b, 9c REALIZED (§25)
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
- Emit stamps v2 whenever a scope is given (`scope_ref=`) or v2 is forced (`envelope_version=2`, the unscoped-v2 form); with neither it emits v1, the legacy carve-out. The service-layer default is `fleet` since 9b (§3, BC-iii).

## 2. Stubs — verifiable holes (9a, REALIZED)

A **stub** is an origin-signed envelope held without its payload bytes (`events.materialized = false`, no payload row). What a stub proves: *"the origin committed to this `payload_digest` at this chain position"* — never byte possession, never content knowledge.

- Stubs chain, verify, advance the head, and replicate onward as stubs; the per-origin gap-free chain survives scope filtering — **a withheld event is a verifiable hole, not a gap**.
- **One materialization predicate** gates reduction in admission AND rebuild (`materialized`): stubs are reducer-inert everywhere; a mixed full/stub log rebuilds to exactly the projections of the full events alone.
- **Upgrade (r2 BC-ii):** re-delivery of a held stub WITH payload routes to the upgrade path, never the idempotent no-op (which would drop the bytes forever — the frontier already counts the stub, so anti-entropy would never re-request it; the frontier is an **envelope-level cursor** by definition). Upgrade byte-verifies against the held commitment, then materializes atomically (payload row + `materialized=true` + reduce, one transaction). Mismatched bytes → `upgrade_digest_mismatch`, reported, non-poisoning (the chain at the head is intact). Upgrade changes materialization eligibility only — identity is fixed.
- The append-only guard on `events` narrows for exactly this one transition: `materialized false→true` + `scope_ref NULL→value` with every hash-relevant column bit-identical (enforced in the trigger). Everything else, and every DELETE, stays forbidden — a node only ever learns bytes it had already committed to by hash.
- Boundary rule (`serve_batch`): a v2 event ships full ONLY to a viewer whose grants include its scope (§3); with no serving context, every v2 event is withheld — least-visible is the fail-direction. v1 events ship full to enrolled-active peers, the legacy carve-out.

## 3. Scope grants and the offer/retain algebra (9b, REALIZED)

Per #113 rev 2 (c), all normative and tested:

- **Grants live in the trust plane** (`grant_scope` / `revoke_scope` / `scope_grants`), receiver-local and forward-only: revoking a scope stops FUTURE payload flow; already-materialized payloads are knowledge, not state to claw back (the key-distrust posture, applied to scopes).
- **The algebra:** sender grant permits **offering** (`serve_batch(events, granted ∩ requested)`); receiver request permits **retaining/materializing** (`admit_batch(requested_scopes=...)`). Every disagreement — either direction — degrades the event to a stub: never a gap, never a chain break, always upgradeable later. Typed reasons: `scope_unrequested` (payload dropped, envelope admitted as stub, non-poisoning — a malicious server cannot push unauthorized content) and `scope_digest_mismatch` (cleartext lies about the hashed commitment — full reject).
- **The least-visible flip (BC-iii), atomic:** `Kawa(default_scope="fleet")` makes every dogfood emit v2-`fleet`; `TrustRegistry.enroll` grants `fleet` by default in the same PR. Fleet-wide visibility is now an explicit grant, never an implicit default. Unscoped v2 (`emit(envelope_version=2)`, no scope) is **node-local materialization** (r2 BC-v): envelope-only replication, no grant can name it, the payload is reachable only at its origin. v1 stays the legacy fleet-visible carve-out (`default_scope=None` emitters).
- **Payload backfill (BC-ii closed end-to-end):** the frontier is an envelope-level cursor — held stubs are counted, so a plain cursor pull would never re-request their bytes. The pull protocol therefore carries a **signed backfill request**: the client names its held-stub positions whose `scope_digest` matches a scope it now requests (`held_stub_positions` — it knows only digests for withheld events, but it knows what it wants and digests it); the server serves those positions through the same offer filter, and admission routes them to the atomic upgrade. Grant-later-upgrade-later works over a plain pull, in-process and over HTTP.
- **Metadata boundary (§12.4):** the request names only the scopes the client asks for; withheld events cross as digest-only stubs; no other scope identifier appears on the wire.

## 4. Segment commitments and archive (9c, REALIZED)

Per #113 rev 2 (d), all normative and tested (`kawa/storage/archive.py`):

- **The attestation claim:** a segment commitment `{origin, from_seq, from_hash, to_seq, to_hash, event_set_digest, policy, attested_at}`, signed by the archiver's node key, attests **verification and custody of a contiguous range** — never authorship (each event already carries the origin's signature). Verification is **four separable layers** (archiver signature → event-set digest → boundary/contiguity → per-event byte verification), so tampering localizes.
- **Detached segments live OUTSIDE the live frontier:** `archive_import` records custody evidence in the security plane (`security_archive_segment`, never counted by anti-entropy) and offers the events to NORMAL admission — they enter the live store only when they chain onto held history (ordinary gap-fill; a tail segment on an empty node stays `detached=true`, verified evidence about unmaterialized history, and chains later when the head arrives).
- **Restore proofs are recorded, never assumed** (§12.3): `scripts/archive_verify.py` re-verifies a file and records an `archive_restore_ok` Observation (value_bool + the #98 source-binding tuple over the file bytes); a corrupted archive records a FAILURE Observation, not silence. The operator/policy loop owns the cadence; the Console `/archive` screen surfaces segments and proof ages. Staleness gates nothing — no GC exists; coupling proof freshness to retention is the future retention-policy step.
- **No pruning path exists** (§11, structure-tested): export reads only; import adds only; the append-only triggers stand untouched.

## 5. Deploy order (operational)

Apply `sql/0010_envelope_v2.sql` BEFORE running the 9a code (emit writes the new columns unconditionally; admission reads `materialized`). Migration-before-code is safe: columns are additive with defaults that describe every pre-step-9 row exactly (`envelope_version=1`, `materialized=true`).

## 6. Realized mapping (§25: implemented, tested — 9a + 9b + 9c)

```text
kawa/storage/archive.py        commitment make/verify (4 layers), archive_export/import (9c)
sql/0011_archive.sql           security_archive_segment custody evidence (frontier-invisible)
scripts/archive_verify.py      restore-proof Observations (ok AND failure recorded)
kawa/console/render.py         /archive screen: segments + proof ages
tests/test_archive.py          catchup, per-layer tamper, detached->gap-fill, failure proof,
                               console smoke, no-pruning structure test (9c)
```

```text
kawa/domain/trust.py           scope grants (grant_scope/revoke_scope/scope_grants),
                               enroll(..., scopes=("fleet",)) default grant (9b)
kawa/application/services.py   Kawa(default_scope="fleet") — the BC-iii emitter flip (9b)
kawa/storage/replication.py    serve_batch(grants) offer filter, admit_batch(requested_scopes)
                               retain filter, held_stub_positions/read_positions backfill (9b)
kawa/adapters/replication_http scopes+backfill in the signed request; offer at the server (9b)
tests/test_scope_grants.py     offer/retain matrix, defense-in-depth, digest mismatch, BC-v,
                               forward-only revocation, fleet default e2e, HTTP backfill (9b)
```

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
