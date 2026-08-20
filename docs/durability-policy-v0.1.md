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
- status file: `~/.kawa/status/replica-pull.status` — named for its UNIT, not
  its script (round-1 review of #228): the OnFailure marker is
  `kawa-replica-pull.service.onfail`, and a status file called `replica.status`
  derives a different component key, so a later good run could never supersede
  the marker. `test_every_unit_status_name_matches_its_own_unit` holds the two
  names together. (Written on EVERY run, success or
  failure; staleness bound `now − status.ts > 2 × cadence`, same as 12A)

## Supervisor (12C)

- tick: T = 60s (#129 rev 2 R2); resident `Type=notify` unit
  (`kawa-supervisor.service`), NOT a timer — the loop holds one connection and
  pings the systemd watchdog every tick
- pull is authoritative (§7.1): each tick reads the ready Work set from
  `current_work`; a wake hint that never arrives degrades to the next tick,
  never to ignorance
- surfacing: a Work FIRST seen ready emits ONE signed `work_surfaced`
  Observation — `value_number` = propagation seconds, `T_surfaced − T_eligible`;
  `T_eligible` = `events.recorded_at` of the eligibility-completing event
  (`current_work.latest_event_id` at first-seen; `ready_at` fallback),
  `T_surfaced` = the tick's status-file timestamp; both from the supervisor
  node's clock, no cross-node math (#129 rev 3 F2)
- propagation bound: `T_surfaced − T_eligible ≤ T + 5s`, measured end-to-end
  once and recorded (R2)
- at-least-once: the surfaced-state file (`~/.kawa/supervisor.state.json`)
  commits AFTER the Observation and status write — kill anywhere and rerun; the
  worst case is a duplicate surfacing, never a silent omission. A Work that
  leaves `ready` is pruned; becoming eligible again surfaces again
- supervision is EXTERNAL (R7): `WatchdogSec=180` (3 missed ticks), 
  `Restart=on-failure`, `OnFailure=kawa-onfail@%n.service`; any tick
  exception ⇒ status ok=false + exit 2 (the loud path, R2)
- bridge (F3): the broker message to `<node>-cc-primary` is a SECONDARY surface,
  deprecated at birth — every status write carries `"bridge":
  "deprecated-active"` until a `bridge_exit_accepted` Observation records one
  operator session served end-to-end by kawa's own presence surface with the
  bridge stopped. Bridge failure lands in `bridge_error`, never fails a tick
- status file: `~/.kawa/status/supervisor.status` (written on EVERY tick,
  success or failure; staleness bound `now − status.ts > 2 × T`)

## Surfaces (12A slice of R5)

- status file: `~/.kawa/status/archive.status` (machine-readable JSON, written
  on EVERY run, success or failure — local-first, broker-independent)
- journald via systemd unit output
- staleness bound: `now − status.ts > 2 × cadence` means the loop is dead —
  computable by ANY external watcher without kawa code

## Reading the status files (2026-08-20)

Every bullet above ends in a status file, and until 2026-08-20 nothing read
them. `kawa-goatcounter.service` failed on the 19th and the 20th, dropped a day
of the site-visit series, and was found by hand on the third day; journald had
the evidence throughout. "Computable by ANY external watcher" was true and
irrelevant — no watcher existed.

- reader: `kawa/nodehealth.py`, printed by `scripts/brief.py` after the Kawa
  brief. Deliberately NOT part of `kawa.brief`: that is a read over replicated
  projections, this is one machine's opinion about its own daemons, and it is
  not an Event
- silent when healthy — a block that prints every session is one the reader
  learns to skip, which is how the same failure goes unread twice
- `OnFailure=kawa-onfail@%n.service` on EVERY resident (one template, instanced
  by the failing unit's own name) catches the case a status file cannot: a
  process killed before it could write anything. A later successful run
  supersedes a marker systemd cannot clear
- status paths resolve through `nodehealth.status_dir()`, honouring
  `KAWA_STATUS_DIR`; a test asserts no resident hardcodes the path. This is the
  filesystem half of the test fence below, added after a test wrote
  `node: "test", ok: false` into the operator's real status directory

- `ok` distinguishes STUCK from DRAINING, not complete from incomplete: a day
  the collector cannot fetch lands in `failed` on every run and stays loud,
  while a bounded backlog draining as designed reports `ok:true` with the
  outstanding days named in the payload. The earlier rule (not-ok whenever the
  series had a hole) fired `OnFailure` on every run of a first backfill —
  a loud path that cries wolf during normal work is a loud path nobody reads

**Not covered, deliberately:** staleness. The bounds above (`now − status.ts >
2 × cadence`) are stated per resident and the reader does not yet evaluate
them, so a resident that is disabled or whose timer never fires produces
neither an `ok:false` nor an `OnFailure` marker and stays invisible. Crash and
reported-failure are covered; *silently stopped* is not. Closing it needs a
cadence per unit that is derived rather than transcribed — retire this
paragraph when that exists.

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
