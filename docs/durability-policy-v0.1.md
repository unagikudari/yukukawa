# Kawa Durability Policy v0.1 — step-12A pinned values

Status: current normative policy — the sha256 of THIS FILE is the `policy_digest`
embedded in every Observation the durability loops emit (#129 rev 2 R8 / rev 3).
Changing any value changes the digest; the measurement lineage visibly splits.

## Archive (12A)

- segment_size: 100 events per origin (only FULL segments are exported; the
  unarchived tail is reported as `lag`, never silently pending)
- cadence: daily (systemd timer `kawa-archive.timer`)
- restore-proof: EVERY existing segment file is re-verified on every run
  (`verify_archive_file`, all four layers); one `archive_restore_proof`
  Observation per run; any per-file failure => value false + error class +
  process exit 2 (the loud path, #129 rev 2 R2)
- retention: segments are append-only; nothing deletes an archived segment
  (retire/compaction is future policy, digest will change)
- storage: `~/.kawa/archive/segments/` (operator data, outside the repo);
  drills use `~/.kawa/archive/drill/` ONLY (#129 rev 2 R6)
- archives are PLAINTEXT — a scoped decision, not an omission (#129 rev 3 F7):
  single-operator trust domain; key custody must be designed BEFORE any
  off-site copy exists, never after
- credential: the node's Ed25519 credential (`~/.kawa/node_credential.json`,
  mode 0600, git-ignored); its public half registered in `~/.kawa/keys.json`

## Replica (12B)

- topology: ONE replica node pulling FROM panoplia (client-driven `pull()` over
  Tailscale IPv6, read-only `kawa_replica` postgres role on the source; the
  replica's own postgres cluster is named by `KAWA_DSN`, the source only by
  `KAWA_SOURCE_DSN` — both fail-closed, nothing is guessed)
- cadence: every 15 minutes (systemd timer `kawa-replica-pull.timer`)
- per cycle: one `replication_frontier_lag` Observation in the REPLICA's local
  log (signed at birth); any admission reject additionally emits ONE
  `replication_admission_reject` Observation naming the reasons + process exit 2
  (#129 rev 3 F6: a reject is loud; a replica is never "green while omitting")
- scoped payloads: the service emits fleet-scoped v2 by default (#113 BC-iii), so
  the replica runs WITH a source-trust MIRROR (`--source-trust`, operator-managed
  copy of the source's serving registry granting this puller `fleet`) — a
  client-driven pull cannot enforce the offer side anyway (the read-only SQL role
  exposes the rows); the mirror computes the same offer/retain sets a serving
  transport would, and is retired when one exists (DEFERRED). Absent the mirror,
  v2 payloads cross as STUBS — the least-visible fail-direction (#113 9b); the
  status file reports `materialized` and `stubs` SEPARATELY every cycle
  (deviation review finding 4)
- attestation: cross-node admission requires signatures (step 8). The pre-12B
  unsigned history is closed by the audited custodian backfill
  (`scripts/attest_backfill.py`, sql/0015 — monotone signature NULL→value, one
  atomic transaction per origin, one `attestation_backfill` Observation).
  Sign-at-birth is mechanized: `emit()` refuses an unattested identity when
  `KAWA_DSN` names a live target (deviation review finding 1)
- terminated origins (`local`, `test`): backfill-signed with EPHEMERAL keys whose
  private halves are discarded after signing (finding 2); every replica enrolls
  them SEALED — `revoke(key, from_seq=head+1)` — so no future event of those
  origins can ever be admitted
- status file: `~/.kawa/status/replica.status` (written on EVERY run, success or
  failure; staleness bound `now − status.ts > 2 × cadence`, same as 12A)

## Surfaces (12A slice of R5)

- status file: `~/.kawa/status/archive.status` (machine-readable JSON, written
  on EVERY run, success or failure — local-first, broker-independent)
- journald via systemd unit output
- staleness bound: `now − status.ts > 2 × cadence` means the loop is dead —
  computable by ANY external watcher without kawa code

## Test fence (12A, #129 rev 3 F1)

- `kawa.storage.db.connect()` is fail-closed: no `KAWA_DSN` => refuse; the
  dogfood database is reached only by naming it explicitly
- fenced role: `kawa_test` — LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE,
  `REVOKE CONNECT ON DATABASE kawa`; full privileges on the `kawa_test_*`
  databases only
- pytest runs UNDER the fenced credential (conftest pins the default test
  DSNs to it); the negative control connects the fenced credential to the
  dogfood DB and requires permission denied
- the fence is an ACCIDENT barrier inside one trust domain, not a security
  boundary against the operator (superuser exists; that is the fleet's
  existing reality)
