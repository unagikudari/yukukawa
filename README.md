# Kawa

**Pre-alpha architecture and dogfood development. Not production-ready.**

> **Change the agent. Keep the work.**

Kawa lets Humans and AI Agents continue shared work without depending on one conversation, one model, one runtime, or one machine.

It preserves **what happened, why decisions were made, what evidence supported them, who was allowed to act, and what actually resulted** — so another Human or Agent can safely continue the work.

A typical Kawa workflow looks like this:

```text
Claude implements a change
        ↓
Kawa preserves the Plan, Work, evidence, and Result
        ↓
Codex reviews it
        ↓
Gemini attacks the assumptions
        ↓
the original Agent/runtime disappears
        ↓
a different Agent connects to Kawa
        ↓
it reconstructs the current situation and continues the available Work
```

The next participant does not need the original chat transcript to become useful.

### What this means for a user

- **Replace an Agent without losing the work.** Claude, Codex, Gemini, local models, Humans, and runtimes are participants rather than the memory.
- **See what should happen next.** Plans preserve intent; Work exposes actionable steps.
- **See why a decision exists.** Evidence, Problems, Plans, Work, Findings, revisions, and Results remain traversable.
- **Do not treat AI inference as fact.** Observation, Claim, and current Fact are separate.
- **Do not treat authentication as permission.** Identity, capability, approval, and execution authority are separate.
- **Do not treat “I ran it” as proof.** Intent, execution, and verified Result are separate.
- **Keep working through partitions.** Divergent histories are preserved and reconciled rather than silently overwritten.
- **Inspect what an LLM retrieved before acting.** Semantic Retrieval is designed to keep relevance, freshness, epistemic standing, and selection distinct.

The deeper architecture exists to keep those promises under replacement, failure, disagreement, and partition.

User-facing positioning and onboarding design: [`docs/user-value-and-onboarding-v0.1.md`](docs/user-value-and-onboarding-v0.1.md).

---

Internally, Kawa is an **event-sourced continuity and authority substrate** for organizations where Humans and AI Agents act over time.

> **Actors pass through Kawa. Events remain. Understanding changes.**

Humans, Agents, models, runtimes, and Nodes are replaceable participants. Kawa preserves the durable evidence, decisions, authority proofs, Plans, Results, and lineage needed for organizational work to continue when those participants change, disappear, partition, or fail.

Kawa is **not** primarily an Agent framework, chat system, or long-term conversation memory. It is a continuity layer for organizational situational awareness and the OODA loop.

## Core model

```text
Events
  ↓ deterministic reducers + policy + evidence / trust
Current Understanding
  ↓
Plan → Work → Execution
  ↓
Result / Observation / Claim / Finding
  ↓
new Events
```

The Event log is the durable Domain Source of Truth. Current state, graph views, Work readiness, health summaries, and other operator views are derived and rebuildable.

## Founding principles

- **Events are the Domain SoT.** Corrections create later Events; history is not rewritten.
- **Current understanding is derived.** Projections, views, indexes, and caches are disposable.
- **Stable semantics. Replaceable mechanics.** Storage profiles, transport, consensus, runtimes, CLI vendors, and process managers must remain replaceable.
- **Identity ≠ Authority.** Identity, capability, approval, and execution authority are separate concepts.
- **Intent ≠ Execution ≠ Verified Result.** A Plan does not prove that an effect occurred.
- **Agents communicate through shared state, not conversation.** Agents pull current context and Work from Kawa.
- **Plan defines intent. Work exposes opportunity. Node/Runtime performs execution. Result records reality.**
- **Unknown is a valid standing.** Missing evidence or proof must not be silently converted into certainty.
- **Preserve history; reconcile understanding.** Partition-side branches, duplicates, and conflicting outcomes remain historical evidence and are interpreted later.
- **Prevent when possible; preserve and reconcile when not.** Kawa must not manufacture an exactly-once illusion where the system lacks enough information to guarantee it.

## Human + AI control plane

Kawa is being designed as an organizational control and continuity plane for accountable Human/AI operations.

```text
Identity
  + Capability
  + Resource / Operation
  + Constraints
  + Approval
  + current Authority standing
        ↓
Authorized execution
        ↓
Result / Receipt / Evidence
```

Authentication is not authorization. A valid Agent identity does not make an Agent correct, benign, or entitled to act.

## Work-driven Agent runtime

The current execution direction is:

```text
Plan
  ↓ derives
Work graph
  ↓
role / capability requirement
  ↓
eligible Runtime on a Node
  ↓
authorization
  ↓
execution
  ↓
Result
  ↓
dependency satisfaction
  ↓
next Work becomes actionable
```

> **Agents do not wait for agents. Work waits for evidence.**

> **Wake the runtime; do not inject the conversation.**

CLI sessions and runtimes are disposable. Kawa state carries continuity.

## Local-first Operator Console

The repository-native Console is intended to make the user-facing value visible first, while keeping the deeper semantics inspectable.

Current and planned surfaces include:

- Situation
- Evidence
- Authority
- Fleet
- Runtime / MCP Analysis
- Semantic Retrieval
- Capability / Skill Growth
- Graph / Decision Lineage

Dashboard state is read from local PostgreSQL projections. Graphs are projections, never a second source of truth.

Run the current Console from a live Kawa database:

```bash
KAWA_DSN=dbname=kawa python scripts/console_serve.py
# open http://127.0.0.1:8099
```

A first-run guided overlay tour is planned to explain the real dashboard by highlighting each live UI area with arrows and short callouts. It will be reopenable from a `?` / Help control and capability-aware so it never advertises functionality the running instance does not have.

Onboarding proposal: [Issue #80](https://github.com/unagikudari/kawa/issues/80)

North-star design handoff: [Issue #63](https://github.com/unagikudari/kawa/issues/63)

## Memory Broker migration

Kawa is greenfield, but migration from Memory Broker is intentionally staged.

> **Run both systems. Keep only one truth path.**

Historical Broker material may coexist as legacy evidence. Active workflow and new semantic writes must have one owning path. The target is Kawa-first writes and reads, with Broker reduced to temporary transport / compatibility / archive duties before retirement.

Migration proposal: [Issue #56](https://github.com/unagikudari/kawa/issues/56)

## Phase 0 implementation

Current Phase 0 direction:

```text
Python + PostgreSQL
```

with a **language-neutral architecture**. Python, FastAPI, tmux, wake transport, CLI vendor adapters, and similar mechanics must not become the specification.

> **Python-first implementation. Language-neutral architecture.**

The first dogfood milestone is:

> **Kawa can use Kawa to implement Kawa.**

Implementation stack proposal: [Issue #57](https://github.com/unagikudari/kawa/issues/57)

### Quickstart (clean clone, Phase 0)

Requires Python ≥ 3.12 and a local PostgreSQL.

First-time PostgreSQL only: ensure your OS user has a role that can create databases
(peer auth over the local socket):

```bash
sudo -u postgres createuser --createdb "$USER"     # skip if `createdb` already works for you
```

Then, from a clean clone:

```bash
python3 -m venv .venv && . .venv/bin/activate      # use python3; venv also avoids PEP-668 errors
pip install -e '.[dev]'                            # pydantic, psycopg[binary]; dev adds pytest, mypy
createdb kawa                                      # the RUNTIME database (event log, console, dogfood)
createdb kawa_test_a                               # the TEST database — pytest TRUNCATEs it every test
cp .env.example .env                               # edit if your DSN differs
export KAWA_DSN=dbname=kawa
python scripts/apply_migrations.py                 # applies sql/0001..NNNN in order, stop-on-error
KAWA_DSN=dbname=kawa_test_a python scripts/apply_migrations.py
pytest -q                                          # DB-backed tests use kawa_test_a (KAWA_TEST_DSN_A)
```

Boot-verified on a clean checkout (fresh venv → migrations → pytest → **all passed, 0 skipped**).
The DB-backed tests **skip** without a database; a genuine boot check requires them to run (zero skips).

> **Never point tests at the runtime database.** The test fixtures TRUNCATE their target, so they
> read only `KAWA_TEST_DSN_A` (default `dbname=kawa_test_a`) and ignore `KAWA_DSN` — the runtime
> DSN cannot select the fixture target even by accident.

### Operator Console

The repository-native Console renders from the **live** `current_*` projections on every request
(no static snapshot embedded):

```bash
KAWA_DSN=dbname=kawa python scripts/console_serve.py     # then open http://127.0.0.1:8099
```

It shows the current plan/work route from disposable projections; drop-and-rebuild changes nothing it depends on. Reads only.

### Component status (not collapsed to one label)

```text
                    DESIGNED  VALIDATED  IMPLEMENTED  INTEGRATED  DEPLOYABLE
Foundational specs     ✓          ✓          n/a          ✓           —
Keystone (op/effect)   ✓          ✓          partial      ✓           —
Phase 0 impl           ✓          ✓          ✓            ✓        ✓ (clean clone)
Console                ✓        partial       ✓            ✓        ✓ (local)
Guided onboarding      ✓          —           ✗            —           —
Semantic Retrieval     ✓          —           ✗            —           —
```

The Console exists as repository code and reads the live Kawa database. Guided onboarding and Semantic Retrieval remain planned/not implemented until their code lands in `main`.

### Document authority

Current-vs-historical map: [`docs/supersession-matrix-v0.1.md`](docs/supersession-matrix-v0.1.md).
Consolidated architecture: [`docs/specification-v0.4.md`](docs/specification-v0.4.md). Superseded
documents in the tree are redirect stubs; their full text remains in Git history.

## Architecture discipline

Kawa is actively reviewed against counterexamples and principle drift.

The project explicitly rejects fixes that merely accumulate:

- special-case branches
- giant mixed-axis enums
- unexplained priority orderings
- magic numbers in Core
- accidental new Sources of Truth
- mechanics leaked into semantics
- guessing where the correct standing is unknown

> **A counterexample should reveal a missing principle, not merely earn a new branch.**

Principle audit proposal: [Issue #62](https://github.com/unagikudari/kawa/issues/62)

## Current status

Kawa is still **pre-alpha**. The repository now contains a runnable Phase 0 substrate and a repository-native local Console, while major distributed/security capabilities remain under active implementation and review.

Major areas already have active designs or formal review around:

- Event-only Source of Truth
- Subject identity and lineage
- Emit / durable write semantics
- epistemic separation: Observation / Claim / Fact
- Human/Agent accountability
- trust, workload identity, capability, approval, and execution authority
- replication and partitioned history
- Authority Cells and awareness dissemination
- Work / Result / Agent runtime orchestration
- execution identity, safe retry, and duplicate reconciliation
- local-first read models and Operator Console
- Semantic Retrieval observability
- Memory Broker migration

The repository is not yet a production security or authority boundary.

## Start here

> **Current position & next work — query Kawa, do not read it from this static list.** Kawa holds
> its own development plan: the `plan-roadmap` Plan is the phase map, and the current phase's
> detail plan (e.g. `plan-node-trust`) holds the next actionable Work. Ask Kawa
> (`work.next(role)`) or open the Operator Console (`KAWA_DSN=dbname=kawa python
> scripts/console_serve.py`). *Actors pass through Kawa; the current position lives in Kawa, not
> in prose that goes stale.* The links below are durable entry points, not the live position.


- User value and onboarding: [`docs/user-value-and-onboarding-v0.1.md`](docs/user-value-and-onboarding-v0.1.md)
- Guided Console tour: [Issue #80](https://github.com/unagikudari/kawa/issues/80)
- Implementation roadmap: [Issue #72](https://github.com/unagikudari/kawa/issues/72)
- Public architecture / adversarial review entrypoint work: [PR #44](https://github.com/unagikudari/kawa/pull/44)
- Work-driven Agent Runtime: [Issue #53](https://github.com/unagikudari/kawa/issues/53)
- Memory Broker migration: [Issue #56](https://github.com/unagikudari/kawa/issues/56)
- Phase 0 implementation stack: [Issue #57](https://github.com/unagikudari/kawa/issues/57)
- Pre-dogfood semantic risk sweep: [Issue #59](https://github.com/unagikudari/kawa/issues/59)
- Architecture principle audit: [Issue #62](https://github.com/unagikudari/kawa/issues/62)
- Semantic Retrieval observability: [Issue #65](https://github.com/unagikudari/kawa/issues/65)
- Console design north-star: [Issue #63](https://github.com/unagikudari/kawa/issues/63)

## Project maxim

> **Preserve Events. Rebuild understanding. Prove authority. Replace participants. Continue the work.**
