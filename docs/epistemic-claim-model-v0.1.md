# Kawa Epistemic Claim Model v0.1

Status: Draft, normative semantic correction from second adversarial review
Scope: How Human/Agent inference enters Kawa without pretending to be Observation or directly rewriting Fact.

## 1. Problem

Kawa distinguishes:

```text
Observation = what was observed
Claim       = what an accountable actor asserts or infers
Fact        = current effective interpretation
```

Fact is a Projection and MUST NOT be directly rewritten.

Treating inference as Observation would erase the distinction between measurement and interpretation.

## 2. Claim

> **Claim = an accountable actor's assertion about a subject.**

A Claim is not automatically true.

The durable Claim record is an Event. A separate enduring Claim entity is not required by default.

## 3. Epistemic separation

```text
Observation
= what a trusted observer/tool measured or received

Claim
= what an accountable Human/Agent asserts or infers

Fact
= what current policy/evidence/authority says should presently be treated as true
```

Therefore:

```text
Observation != Claim
Claim != Fact
Fact != Source of Truth
```

Events remain the Domain SoT.

## 4. Event

The canonical Event is:

```text
claim.recorded
```

The authoritative Event representation uses the common Event envelope plus typed payload:

```yaml
event_id: evt_...
event_type: claim.recorded
subject_ref: ref
actor_ref: authenticated claimant

payload:
  predicate: namespaced string
  value: typed value | unknown
  rationale: string | omitted
```

Evidence is expressed by Event links such as `based_on` and `supports`.

A replacement/correction is another `claim.recorded` Event with `corrects` or `supersedes` pointing to the earlier Claim Event. No historical Claim is edited.

## 5. Public semantic projection and naming

Public read models MAY use natural names when the underlying Event-envelope role is unambiguous.

Canonical mapping:

```text
Public Claim.ref        <-> Event.event_id
Public Claim.subject    <-> Event.subject_ref
Public Claim.claimant   <-> Event.actor_ref
Public Claim.predicate  <-> event_claim.predicate
Public Claim.value      <-> event_claim.value_*
Public Claim.rationale  <-> event_claim.rationale
Public Claim.evidence   <-> event_links based_on/supports
```

This is a presentation mapping, not two competing schemas.

Rules:

- storage/Event contracts use `event_id`, `subject_ref`, `actor_ref`;
- public semantic projections may use `ref`, `subject`, `claimant`;
- documents MUST NOT mix the two naming layers inside one schema example without stating the mapping;
- MCP write schemas use semantic names for caller-supplied values and omit infrastructure-attached identity fields entirely where possible.

The same principle applies to Observation (`observer` maps to `observer_ref`) and other public projections.

## 6. Why one Event type

Do not create `claim.created`, `claim.updated`, `claim.revised`, and `claim.corrected` unless independent Claim lifecycle semantics are demonstrated.

A Claim is normally a point-in-time accountable assertion. The Event ID is sufficient.

## 7. Agent-facing minimization

A Skill or Agent intending to influence current Fact understanding supplies only semantic assertion values not otherwise derivable.

Example:

```yaml
predicate: software.package.risk
value: vulnerable
rationale: Installed version is within the affected range.
```

If current Work uniquely identifies the subject, Project, evidence, and intended output kind, those values are attached automatically.

The Agent does not supply:

```text
actor_ref / claimant authority
workload_ref
node_ref
claim/Event ID
Fact ID
Fact status
Fact authority
schema version
stale-write token
```

## 8. Fact reducer

Fact Projection MAY consider:

```text
Observations
Claims
Results
other typed Events
source/actor authority policy
scope
time validity
conflict policy
```

A Claim does not become Fact merely because it was emitted.

Conflicting Claims or Claim-vs-Observation disagreement remain visible when policy cannot resolve them.

## 9. Deterministic collectors do not emit Claims

Ansible, vulnerability scanners, system commands, sensors, and other deterministic collectors normally emit `observation.recorded` through trusted adapters.

An LLM interpreting those observations normally emits `claim.recorded` when it needs to make a structured assertion.

```text
scanner output
→ Observation
→ LLM/Human reasoning
→ Claim
→ deterministic Reducer/Policy
→ Fact
```

## 10. Security

Prompt text cannot turn a Claim into authority.

The authoritative claimant identity is infrastructure-attached as Event `actor_ref`.

The caller cannot claim to be a scanner, Human approver, privileged Workload, or another Agent by setting semantic fields.

Claim confidence/rationale, if present, is assertion content and MUST NOT be confused with trusted authority.

## 11. Fact rewrite semantics

There is no direct Fact rewrite.

A request such as:

```text
update the Fact that X = Y
```

must resolve to one of:

```text
record new Observation
record new Claim
record correction/supersession evidence
change deterministic Fact policy through an authorized policy operation
```

The current Fact changes only through Reducer reconstruction.

## 12. Acceptance tests

```text
An LLM inference cannot be mislabeled as deterministic Observation by ordinary write input.
A Claim carries infrastructure-attached claimant identity.
Public Claim.claimant and storage actor_ref are documented as one semantic role.
A Claim does not automatically become Fact.
A new Claim can supersede an earlier Claim without editing history.
Conflicting Claims can produce a conflicted Fact.
A deterministic collector emits Observation, not Claim.
An Agent can contribute to Fact state without writing a Fact table.
```

## 13. Core rule

> **Observe what happened. Claim what you infer. Derive what is currently accepted as Fact.**
