# Kawa Retrieval Recall Gate v0.1 — step-11 Phase addendum

Status: Draft, current normative addendum — 11A-1 (instrument) REALIZED; 11A-2 (measurement) and 11B (conditional backend) PLANNED per §25
Realizes: `specification-v0.5.md` §10.6's conditional per roadmap step 11 (issue #122, two-round gate + BC-1..BC-6)
Companions: `kawa/retrieval.py` (§10 SQL-first, the thing measured), `scoped-replication-and-archive-v0.1.md` (the 9a predicates 11B's scope invariant reuses)

> **Vector indexing may be added when measured query classes demonstrate insufficient recall.** The gate is the machine that makes that sentence decidable.

## 1. The instrument (11A-1, REALIZED — `kawa/retrieval_eval.py`)

- **Corpus** (BC-3): versioned, digested; per-query labels in retrieve()'s REF SPACE + labeling and sampling provenance; the canonical classes are `QueryClass.purpose` (5); quotas enforced by a structure check BEFORE measurement (≥8/class ⇒ ≥40 queries; ≥1/3 failure-sourced; ≥1/4 cross-reference).
- **Witnesses** (BC-1): every miss carries per-backend MACHINE proofs — the real FTS predicate run against the record, BFS shortest-path length vs depth over resolved links (node-capped, cap reported), typed-relation existence. `machine_cause` derives from witnesses alone: any reachable ⇒ never semantic.
- **Double-blind** (BC-2): `blind_export` strips the author's causes; a different agent re-diagnoses from the same witnesses; only AGREED semantic misses enter the GO arithmetic, disputes are excluded.
- **The verdict is computed**: GO iff some ≥8-query class's **agreed-semantic misses alone** push counted recall under 0.75 AND that survives a one-label flip. Otherwise NO_GO — which COMPLETES roadmap step 11 (§10.6's condition machine-judged false), with the harness standing as a regression. Unadjudicated candidates ⇒ PENDING_ADJUDICATION: the gate cannot close on one person's judgment.
- **Prose cannot override** (r1 (j)): `guard_result` refuses a Result whose verdict differs from the harness output; overriding requires a new gate issue.

## 2. The measurement (11A-2, PLANNED)

A separate PR/commit (BC-6): the real corpus (real-log-sourced, operator-labeled, quota-checked), the blind adjudication round by a second agent, `scripts/measure_recall.py --record` emitting `retrieval_recall` + `retrieval_gate_verdict` Observations source-bound to the corpus digest, and the Result citing only those ids.

## 3. The conditional backend (11B, PLANNED — only on GO)

Per #122 rev 2 (f)-(i) + BC-4/BC-5: materialized-only embedding (a stub has no bytes to embed — no side-channel substrate), pre-filter semantics with zero-shadowing negative controls, `model_identity` as a canonical digest, the embedding table as §12.2 derived materialization with audit/frontier, frozen-corpus paired delta + an HLC-anchored post-freeze holdout.

## 4. Realized mapping (§25 — 11A-1)

```text
kawa/retrieval_eval.py     corpus validator (quotas), witnesses, measure() + verdict,
                           blind_export, guard_result
scripts/measure_recall.py  CLI: report JSON, blind package, --record Observations
tests/test_retrieval_eval.py  quota negatives, witness proofs, verdict phases incl. the
                           BC-2 arithmetic (agreed-only counting, dispute sensitivity),
                           blind stripping, prose-override refusal
```
