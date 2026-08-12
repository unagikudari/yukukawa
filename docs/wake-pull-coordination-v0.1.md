# Kawa Wake/Pull Coordination v0.1 (Phase-0 realized) — the ghost-participant fix

Status: Draft, current normative — realizes `specification-v0.5.md` §7/§7.1/§17 as an in-process subset (roadmap step 7, #108)
Companions: `mcp-participant-introduction-v0.1.md` (the sessions/discovery this coordinates), `identity-credential-lifecycle-v0.2.md` (session identity), `specification-v0.5.md` §7/§7.1/§17

> **Push for liveness. Pull for correctness. Wake the runtime, not the model context.**

## 1. What this closes: the ghost-participant problem, structurally

A "ghost" is a participant that looks present (fresh heartbeat / open session) but never does the work — the pattern that, on the legacy broker, forces an operator to manually cancel and re-route a dispatched task. Step 6 removed half the problem (a participant is discovered by *authorized capability from a verified session*, never by heartbeat). Step 7 removes the other half:

- **Work is pulled, not pushed** — the authoritative path is `work_next_for_participant` (§16.5), never a wake.
- **A wake is only a hint** — lost / duplicated / delayed / reordered wakes never change authoritative Work state.
- **A held Work is released on loss of reachability** — explicitly on an observed session drop, or by lease expiry on silence.

So a ghost lane cannot hold or lose Work: if it never claims, the Work stays discoverable by others (step 6); if it claims then goes silent, the lease expires and another participant rediscovers it; if its session is observed to drop, its claims release immediately. **No operator action, ever.**

## 2. Scope (§25): in-process, guarantees named

| REAL now | DEFERRED (named) |
|---|---|
| `WakeHint` + `WakeBus` (non-authoritative, in-process) | network wake transport / real supervisor push |
| `ClaimRegistry` claim + lazy lease expiry | automatic CLI-agent launch (§7.1 runtime profile) |
| reachability release on observed drop (§17) | cross-node single-writer agreement (→ step 10 authority) |
| pull stays authoritative; claim is a discovery exclusion | durable claim survival across a Kawa restart (in-memory) |

Trust/coordination plane — **NOT new Domain Events**, never authority, never exactly-once.

## 3. Wake is a hint (§7)

`WakeHint = {work_ref, reason}` — structurally cannot carry instruction/record prose (the §7 anti-injection boundary; the participant PULLS the full contract via step 6). `WakeBus.emit/drain` touch no projection and no claim: a wake cannot create, claim, reorder, or mark Work ready. Its only effect is to prompt a pull, and pull is the sole authoritative path. Poll-only participants (no wake) are equally valid.

## 4. Claim is advisory dispatch coordination — readiness ≠ discoverability

A claim is **single-store, advisory, dispatch-only, scoped to Work selection**, carrying no effect identity and no side-effect authority (authority is step 10; exactly-once is step 8). Two surfaces, kept distinct:

- **readiness** — the base eligibility predicate, UNCHANGED and claim-free: a claimed Work stays `execution='ready'` and the global `work_next` still returns it.
- **discoverability** — `work_next_for_participant` adds a temporary exclusion: a non-holder participant does not see an actively-claimed Work.

**Lazy expiry, one rule:** `ClaimRegistry.holder(work_ref, now)` treats `expires_at <= now` as ABSENT (and lazily prunes it). Because every consult goes through `holder`, an expired/ghost claim never suppresses a contending participant's later pull — which is precisely why no background reaper is needed for correctness. A stale claim record for Work nobody pulls is a storage-retention detail, not a correctness one. A live holder blocks a different session (presumed-live during the lease — slow vs dead is undecidable, so exclusion is the safe coordination choice); the same session may re-claim to refresh the lease.

## 5. Reachability release — two mechanisms, no §17 overclaim

- **observed drop** → `release_session(session_id)` frees that session's claims immediately (`session_dropped`).
- **silence** → the claim releases only by lease expiry (`lease_expired`), via §4's pull-time rule.

Phase-0 has **no silence detector** — §17's "loss of reachability is mechanically actionable" holds only for *observed* drops; silence is handled by the lease. The reasons are distinguishable (recovery immediacy: `t=0` vs `t=T_lease`; and audit).

## 6. Non-goals (explicit, no overclaim)

- Step 7 reduces duplicate **live** dispatch within one running broker instance.
- Step 7 does **NOT** guarantee exactly-once effects. A restart while a claim is held can rediscover the Work and MAY cause duplicate execution unless the operation is idempotent or later **effect identity (step 8)** gates it. In-memory claims vanishing on restart is fail-OPEN toward rediscovery (availability), not duplicate-execution safety.

## 7. Realized mapping (§25: implemented, tested)

```text
kawa/domain/coordination.py   WakeHint / WakeBus / Claim / ClaimRegistry
services.work_next_for_participant(registry, session_id, claims=, now=)   claim-aware discovery
tests/test_coordination.py    wake-hint structure + best-effort bus; single-live-holder + lazy
                              expiry + re-claim + release; claim=discovery-not-readiness;
                              GHOST B (silent → lease expiry → rediscovery) and GHOST C
                              (observed drop → immediate release → rediscovery), zero operator
                              action; wake-count-invariant pull
```
