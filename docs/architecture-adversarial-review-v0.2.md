# Kawa Adversarial Architecture Review v0.2

Status: Internal adversarial pre-gate review
Date: 2026-08-10
Scope: Review of architecture after PR #1 first-review findings were incorporated.

## 1. Method

This review treats the current design as wrong until each security/correctness claim survives a concrete falsification attempt.

Primary targets:

```text
identity and credential theft
scope confusion
approval bypass
stale-write confusion
LLM input/provenance spoofing
Fact epistemic integrity
MCP schema ambiguity
Memory Broker migration/rewrite justification
```

This review is not considered independent from the architecture authoring process. It is a pre-gate adversarial pass. F-001 still requires an independent external/model review before implementation begins.

## 2. Findings

### AR2-H01 — Bearer JWT text could impersonate a Workload

Severity: HIGH
State: resolved in this pass

Falsification:

If authentication accepts a bearer JWT whose `sub` identifies a Workload, copying that JWT to another process can impersonate the Workload until expiry even though the Agent cannot edit `workload_ref`.

Fix:

`identity-credential-lifecycle-v0.1.md` now requires production Workload credentials to be proof-of-possession or authenticated-channel bound. JWT text alone is insufficient.

Examples include mTLS-bound tokens, DPoP, or equivalent runtime-held key binding.

### AR2-H02 — Approval policy could omit a critical binding

Severity: HIGH
State: resolved in this pass

Falsification:

The prior wording allowed policy to choose the “relevant subset” of approval fields. A defective or manipulated policy could bind Plan identity while omitting target Resource or operation set, allowing authority substitution.

Fix:

`approval-binding-v0.1.md` now defines a mandatory minimum binding set:

```text
Plan identity
Plan semantic revision/fingerprint
resource/target scope
operation set
Human approving authority
expiry
```

Policy may add restrictions, not remove this minimum for high-risk Approval.

### AR2-H03 — Agent inference had no honest Event path into Fact

Severity: HIGH
State: resolved in this pass

Falsification:

Fact cannot be directly written, correctly. But the prior taxonomy had no semantic place for an Agent/Human inference that is neither a deterministic Observation nor an execution Result.

Without such a path, implementations would be pushed toward:

```text
mislabel inference as Observation
or
introduce an ungoverned direct Fact write
```

Fix:

`claim.recorded` is now integrated through the normative schema chain:

```text
Core Logical Schema v0.3
Event Taxonomy v0.2
Reducer / Projection Contract v0.2
PostgreSQL Physical Schema v0.3
MCP Contract v0.2
```

The epistemic rule is:

```text
Observation = measured/received evidence
Claim       = accountable assertion/inference
Fact        = current derived interpretation
```

A Claim does not automatically become Fact.

### AR2-M01 — Stale basis could be confused across concurrent Work

Severity: MEDIUM
State: resolved in this pass

Falsification:

A session-global basis could be overwritten or reused when one Workload handles Plan A and Plan B concurrently.

Fix:

`stale-write-guard-v0.1.md` now binds basis to exact subject, semantic action, scope, Work/Review/Finding context, frontier, and policy/schema identity as applicable. One Work continuation cannot authorize another.

### AR2-M02 — Generic `fields` object undermined MCP self-description

Severity: MEDIUM
State: resolved in this pass

Falsification:

A single `kawa.emit(event_type, fields:any)` tool does not let a new LLM infer Event-specific required input from tool schema alone and recreates a generic document escape hatch at the interface layer.

Fix:

`mcp-contract-v0.2.md` requires a discriminated typed Event union (`oneOf` or equivalent). Generic arbitrary `fields` is non-conforming.

### AR2-M03 — MCP required values Kawa should generate or infer

Severity: MEDIUM
State: resolved in this pass

Falsification:

The prior MCP example required the LLM to provide opaque `subject` IDs and repeated Project/context values even for newly-created entities or Work-bound actions.

Fix:

`mcp-contract-v0.2.md` requires Kawa to generate new entity refs and infer event type, subject, scope, implied links, schema version, identity, basis, and provenance whenever uniquely determined.

### AR2-M04 — Extend-vs-rewrite claim needed evidence from the actual Broker

Severity: MEDIUM
State: resolved provisionally

Actual Memory Broker implementation history was inspected.

Evidence shows both substantial conceptual reuse opportunities and significant foundational divergence, particularly generic memory/tag semantics, multi-writer replication constraints, distributed read/write lanes, path-specific provenance correction, and compatibility burden.

Current decision remains greenfield Domain core + incremental coexistence, with an explicit reopen rule if prototype evidence favors extension.

See `memory-broker-architecture-evidence-v0.1.md`.

## 3. Result of this pass

This pass found:

```text
CRITICAL: 0
HIGH:     3
MEDIUM:   4
```

All HIGH and MEDIUM findings discovered in this pass now have architecture-level resolutions.

This is still NOT the final architecture gate pass because the review is not independent from the authoring process.

## 4. Remaining gate blockers

```text
F-012 historical-document supersession cleanup
independent adversarial review required by F-001
```

No known CRITICAL/HIGH architecture finding is intentionally left open at this point.

## 5. Specific independent-review attacks to run next

```text
steal a valid Workload JWT and replay it from another runtime
swap target Resource after Human Approval
reuse a stale Work basis across two concurrent Plans
make an LLM inference appear to be scanner Observation
attempt cross-Project search via omitted scope
attempt to make a Claim authoritative merely by high self-reported confidence
import ambiguous Broker data and see whether it silently becomes Fact
replay imported/execution Events and check for repeated side effects
```

## 6. Core conclusion

The architecture now has an explicit epistemic chain:

```text
Observation
→ Claim when reasoning adds interpretation
→ Fact Projection
→ Problem / Plan / Review / Action
→ Result
→ new evidence
```

> **A system that distinguishes Observation from Fact must also distinguish inference from Observation.**
