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
