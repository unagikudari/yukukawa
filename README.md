# Kawa

**Pre-alpha architecture and dogfood development. Not production-ready.**

Kawa is an **event-sourced continuity and authority substrate** for organizations where Humans and AI Agents act over time.

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

The planned Console exposes multiple independent dimensions rather than collapsing the system into one red/green health bit:

- Situation
- Evidence
- Authority
- Fleet
- Runtime / MCP Analysis
- Capability / Skill Growth
- Graph / Decision Lineage

Dashboard state is read from local PostgreSQL projections. Graphs are projections, never a second source of truth.

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

```bash
python -m venv .venv && . .venv/bin/activate      # avoids PEP-668 externally-managed errors
pip install -e '.[dev]'                            # pydantic, psycopg[binary]; dev adds pytest, mypy
createdb kawa                                      # a local database named 'kawa'
cp .env.example .env                               # edit if your DSN differs
export KAWA_DSN=dbname=kawa
python scripts/apply_migrations.py                 # applies sql/0001..NNNN in order, stop-on-error
KAWA_DSN=dbname=kawa pytest -q                      # DB-backed tests run against that database
```

Boot-verified on a clean checkout (fresh venv → migrations → pytest → **13 passed, 0 skipped**).
The DB-backed tests **skip** without a database; a genuine boot check requires them to run (zero skips).

### Component status (not collapsed to one label)

```text
                    DESIGNED  VALIDATED  IMPLEMENTED  INTEGRATED  DEPLOYABLE
Foundational specs     ✓          ✓          n/a          ✓           —
Keystone (op/effect)   ✓          ✓          partial      ✓           —
Phase 0 impl           ✓          ✓          ✓            ✓        ✓ (clean clone)
Console                ✓        partial       ✗            —           —      (no serving code yet)
Semantic Retrieval     —          —           ✗           —           —
```

Deploying this repository yields the Phase 0 substrate and its tests. It does **not** yet yield a
Console — the Console is DESIGNED (`docs/console-read-model-v0.1.md`, `docs/design/`) but has no
serving code.

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

Kawa is still in **founding architecture review + Phase 0 dogfood preparation**. Major areas already have active designs or formal review around:

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
- Memory Broker migration

The active architecture work currently lives primarily on the `agent/specification-v0.2` line and associated RFC / review Issues and PRs. The repository is not yet a production security or authority boundary.

## Start here

- Public architecture / adversarial review entrypoint work: [PR #44](https://github.com/unagikudari/kawa/pull/44)
- Work-driven Agent Runtime: [Issue #53](https://github.com/unagikudari/kawa/issues/53)
- Memory Broker migration: [Issue #56](https://github.com/unagikudari/kawa/issues/56)
- Phase 0 implementation stack: [Issue #57](https://github.com/unagikudari/kawa/issues/57)
- Pre-dogfood semantic risk sweep: [Issue #59](https://github.com/unagikudari/kawa/issues/59)
- Keystone rev2 risk envelope: [Issue #61](https://github.com/unagikudari/kawa/issues/61)
- Architecture principle audit: [Issue #62](https://github.com/unagikudari/kawa/issues/62)
- Console design north-star: [Issue #63](https://github.com/unagikudari/kawa/issues/63)

## Project maxim

> **Preserve Events. Rebuild understanding. Prove authority. Replace participants. Continue the work.**
