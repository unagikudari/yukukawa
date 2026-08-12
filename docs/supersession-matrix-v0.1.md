# Kawa Supersession Matrix v0.1

Status: Current repository authority map
Purpose: Make document authority explicit while historical drafts remain available through Git history.

This file is the first document in the recommended reading path.

## Current architecture entry point

```text
specification-v0.5.md
  supersedes specification-v0.4.md
  supersedes specification-v0.3.md / v0.2 transitively
```

`specification-v0.5.md` is the current consolidated architecture specification (epistemic and runtime refoundation: Event / Observation / Claim / Plan nucleus; no Kawa-owned Fact or Current Understanding; Situational Awareness belongs to observers). Detailed contracts below remain normative where they refine v0.5 without contradicting it; the epistemic nucleus is realized by `epistemic-claim-model-v0.2.md` (#97); `reducer-projection-contract-v0.2.md` and the FROZEN `consistency-and-authority-v0.1.md` still carry pre-v0.5 `Fact` wording and MUST be read through v0.5 §2 — their revision rides the projection-contract work (roadmap step 4) and the authority work (step 10) respectively.

## Current normative candidates

```text
core-logical-schema-v0.3.md
  supersedes core-logical-schema-v0.2.md and v0.1

event-taxonomy-v0.2.md
  supersedes event-taxonomy-v0.1.md

reducer-projection-contract-v0.2.md
  supersedes reducer-projection-contract-v0.1.md

postgresql-physical-schema-v0.3.md
  supersedes postgresql-physical-schema-v0.2.md and v0.1

mcp-contract-v0.2.md
  supersedes mcp-contract-v0.1.md

wizard-error-guidance-v0.2.md
  supersedes wizard-error-guidance-v0.1.md
```

## Current supporting contracts

```text
consistency-and-authority-v0.1.md
operation-effect-identity-v0.8.md
subject-identity-and-lineage-v0.1.md
event-log-and-replication-v0.1.md
emit-enforcement-contract-v0.1.md
security-model-v0.1.md
identity-credential-lifecycle-v0.1.md
scope-resolution-v0.1.md
approval-binding-v0.1.md
stale-write-guard-v0.1.md
epistemic-claim-model-v0.2.md
  supersedes epistemic-claim-model-v0.1.md (Fact projection abolished — v0.5 §2.6, #97)
deterministic-observation-ingestion-v0.1.md
llm-write-input-minimization-v0.1.md
git-plan-workspace-v0.1.md
github-workflow-integration-v0.1.md
memory-broker-extend-vs-kawa-v0.1.md
prototype-vertical-slice-v0.1.md
memory-broker-migration-coexistence-v0.1.md
memory-broker-architecture-evidence-v0.1.md
architecture-review-findings-v0.1.md
architecture-adversarial-review-v0.2.md
publication-boundary.md
document-versioning-and-reading-path-v0.1.md
```


**Authority notes.**
- `consistency-and-authority-v0.1.md` is the re-frozen ③④ (the F7/F8/F9 consume-once correction). An earlier same-named freeze that lacked that fix is superseded and lives only in Git history.
- `operation-effect-identity-v0.8.md` is the standalone-consolidated keystone; its v0.1–v0.7 drafts are historical stubs below.

## Operator surface — DESIGNED, NOT IMPLEMENTED

The Console read-model and design are current DESIGN authority, but **no Console serving code exists in the tree** (NOT_IMPLEMENTED). Deploying Kawa does not yet yield a Console.

```text
console-read-model-v0.1.md               — DESIGNED (read-model spec; projects, stores no authority)
docs/design/kawa-console-design-brief.md — DESIGNED (semantic + visual invariants)
docs/design/kawa-console-screen-map.md   — DESIGNED (screen → read-model contract; some column bindings pending the flattened keystone)
docs/design/kawa-console-north-star.svg  — DESIGNED (visual north star)
```
## Historical redirect stubs

The following files are not current authority. Their former full content remains in Git history; the current tree contains only explicit redirect stubs where applicable.

```text
specification-v0.2.md
specification-v0.3.md
core-logical-schema-v0.1.md
core-logical-schema-v0.2.md
event-taxonomy-v0.1.md
reducer-projection-contract-v0.1.md
postgresql-physical-schema-v0.1.md
postgresql-physical-schema-v0.2.md
mcp-contract-v0.1.md
wizard-error-guidance-v0.1.md
operation-effect-identity-v0.1.md
operation-effect-identity-v0.2.md
operation-effect-identity-v0.3.md
operation-effect-identity-v0.4.md
operation-effect-identity-v0.5.md
operation-effect-identity-v0.6.md
operation-effect-identity-v0.7.md
```

Every superseded file in the current tree MUST contain:

```text
Status: Superseded
Superseded by: <current document>
```

## Authority rule

If README, this matrix, `specification-v0.5.md`, a current detailed contract, or a superseded stub disagree about authority, the repository is inconsistent and the architecture gate remains open.

A current detailed contract may refine the consolidated specification but MUST NOT silently contradict it.

A historical draft MUST NOT remain as a fully readable competing specification in the current tree once superseded; Git history is the archive.

> **History remains in Git. Authority remains obvious in the tree.**
