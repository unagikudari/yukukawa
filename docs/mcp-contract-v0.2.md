# Kawa MCP Contract v0.2

Status: Draft, current normative interface contract
Supersedes: `mcp-contract-v0.1.md`
Scope: Minimal LLM-facing interface for orientation, read/search, durable Event emission, and continuation of eligible Work.

## 1. Purpose

A capable LLM should be able to use Kawa from tool names, schemas, current state, and deterministic Wizard guidance without learning Kawa-specific mechanics.

> **Require intent. Infer context. Attach trust.**

The MCP surface exposes semantic choices and hides values that Kawa can determine authoritatively.

## 2. Core tools

```text
kawa.bootstrap
kawa.get
kawa.search
kawa.emit
kawa.work.next
```

This is the complete v0.2 core surface.

Convenience adapters MAY compile to these semantics, but `kawa.emit` remains the only canonical public Domain write primitive.

## 3. Security boundary

All protected operations inherit:

- `security-model-v0.1.md`
- `identity-credential-lifecycle-v0.1.md`
- `scope-resolution-v0.1.md`

Production Workload authentication requires the configured proof-of-possession/channel-bound credential profile. A copied JWT string alone is not sufficient authentication.

Processing order:

```text
request
→ authenticate identity + credential possession
    invalid → minimal rejection, no Wizard
→ authorize discoverability/action
→ resolve scope
→ resolve semantic context
→ execute
→ success OR authorized Wizard guidance
```

## 4. Input minimization rule

A caller MUST NOT be asked to provide a value Kawa can determine uniquely and authoritatively.

Kawa should infer or attach, when unambiguous:

```text
project/scope
subject for current Work
new entity reference
actor/workload/node identity
observer identity for deterministic collectors
event schema version
causation/correlation
observation_method from trusted adapter
stale-write basis
approval binding/security metadata
links implied by current Work or causation
```

If more than one semantic choice remains possible, Kawa MUST stop with `needs_selection` or `needs_input` rather than guess.

## 5. `kawa.bootstrap`

Purpose: orient the authenticated caller to the minimum current context needed to continue.

Input SHOULD normally be empty.

An explicit `project` selector is accepted only when the caller is choosing among discoverable authorized Projects.

If trusted context resolves exactly one Project, it is filled automatically. If multiple remain plausible, return `needs_selection`.

Output SHOULD contain:

```yaml
status: ok
project:
  ref: kawa://project/...
  name: string
summary: string
must_know: []
ready_work: []
next_allowed_actions: []
```

`must_know` SHOULD normally contain at most 10 items.

## 6. `kawa.get`

Purpose: read one authorized Kawa reference.

Input:

```yaml
ref: kawa://...
```

Current effective state is default. Historical retrieval is explicit.

The response SHOULD expose semantic state, evidence/relations, provenance where relevant, and `next_allowed_actions`.

Concurrency/basis mechanics are not exposed as concepts the LLM must manage.

## 7. `kawa.search`

Purpose: find authorized Kawa state relevant to a semantic query.

Input:

```yaml
query: string
kind: project | problem | plan | observation | claim | fact | review | finding | event | result | omitted
project: ref | omitted
history: false | true
limit: integer | omitted
```

Rules:

```text
history = false by default
limit <= 20 by default
```

`project` omission follows `scope-resolution-v0.1.md`:

```text
unique authorized scope → fill
multiple plausible scopes → needs_selection
no scope → reject/not_found
```

Omission never means global search.

Authorization/scope/lifecycle filtering occurs before retrieval/ranking.

## 8. `kawa.emit`

Purpose: record one durable Domain Event through one obvious write path.

### 8.1 Public schema must be typed

A conforming MCP schema MUST expose Event-specific semantic fields through a discriminated union (`oneOf` or equivalent typed mechanism).

A generic free-form object such as:

```yaml
fields: arbitrary object
```

is non-conforming.

The schema should let a capable LLM see what semantic information each Event type requires.

### 8.2 Caller supplies only unresolved semantic intent

Conceptually:

```yaml
event_type: string | inferred
subject: ref | inferred/generated
project: ref | inferred
<event-specific semantic fields>
<explicit semantic links only when not implied>
```

`event_type`, `subject`, `project`, and links SHOULD be omitted from the actual call when trusted Work/causation context determines them uniquely.

Examples:

#### Raise a new Problem from current Project context

Caller may need to supply only:

```yaml
statement: Replication health is inconsistent across nodes.
rationale: Recent deterministic observations disagree.
```

Kawa generates the Problem reference and attaches Project, actor, identity, time, schema version, causation, and implied evidence links when uniquely known.

#### Revise the Plan for current Work

Caller may need to supply only:

```yaml
rationale: Address the unresolved security finding.
```

If Work uniquely binds the Plan and Finding, Kawa attaches subject, Project, `based_on`, Work basis, and trusted identity automatically.

#### Record an inferred proposition

When an Agent/Human infers something that is not a deterministic Observation, the typed Event is `claim.recorded`.

If Work uniquely identifies subject/evidence, caller input may be only:

```yaml
predicate: software.package.risk
value: vulnerable
rationale: Installed version is within the affected range.
```

Kawa attaches authenticated claimant identity and implied evidence links. The Claim does not automatically become Fact.

### 8.3 New entity references

For events that establish a new enduring semantic identity, such as:

```text
project.created
problem.raised
plan.proposed
review.started
finding.raised
```

Kawa SHOULD generate the canonical reference unless an external identity mapping contract explicitly requires otherwise.

Observation, Claim, Approval grant, and Result normally use their Event identity and do not require the LLM to invent another ref.

### 8.4 Trusted fields are never caller authority

The public write schema MUST NOT accept authoritative caller values for:

```text
node_ref
workload_ref
authoritative actor_ref
authoritative observer_ref
recorded_at
local_sequence
schema_version
security timestamps
approval validity
credential/JWT fields
stale-write basis internals
observation_method for trusted deterministic collectors
Claim authority/claimant identity
```

### 8.5 Schema version

The server selects the current supported Event schema version from the Event type and compatibility policy.

The ordinary LLM does not choose `schema_version`.

If an explicit historical/import schema is needed, that is a migration/import interface, not ordinary `kawa.emit`.

### 8.6 State-dependent writes

State-dependent emits inherit `stale-write-guard-v0.1.md`.

The matching trusted basis is attached automatically from exact Work/request context.

A stale write returns a semantic conflict, not a CAS/ETag error.

### 8.7 Atomic Event + links

One `kawa.emit` call records one Domain Event and its semantic links atomically with respect to Event acceptance.

No partially accepted Event may exist without required links when the Event schema says those links are mandatory.

## 9. `kawa.work.next`

Purpose: return the next currently eligible Work for the authenticated Workload.

Input SHOULD normally be empty.

Optional selectors such as `project` or `kind` are used only when multiple authorized choices exist or the caller intentionally narrows discovery.

Output:

```yaml
status: ok
work:
  ref: kawa://work/...
  kind: adversarial_review
  project: kawa://project/...
  plan: kawa://plan/...
  why: string
  required_output: review
next_allowed_actions:
  - kawa.get
  - kawa.emit
```

Internally Work establishes exact scope, capability context, coordination ownership, and stale-write basis. Those mechanics are not LLM inputs.

## 10. Wizard outcomes

Authenticated recoverable failures use deterministic typed guidance:

```text
ok
needs_input
needs_selection
conflict
blocked
approval_required
capability_required
precondition_failed
not_found
forbidden
retryable
unsafe
```

Invalid authentication receives no Wizard.

Wizard only requests values that cannot be authoritatively inferred.

## 11. References

Canonical opaque forms include:

```text
kawa://project/...
kawa://problem/...
kawa://plan/...
kawa://review/...
kawa://finding/...
kawa://event/...
kawa://resource/...
kawa://work/...
```

Observation/Claim/Approval-grant/Result Event records use `kawa://event/...` unless a future independent lifecycle justifies a new reference family.

Opaque identifiers are generated by Kawa or a trusted identity-mapping adapter, not by LLM creativity.

## 12. Explicit unknown and omission

Use omission when a field does not apply.

Use explicit typed `unknown` when a semantic value applies but is not known.

Do not overload `null` with multiple meanings.

## 13. Avoid ambiguous booleans

Prefer semantic state:

```text
approval = pending | valid | stale | revoked | expired | not_required
revalidation = clear | required
```

over ambiguous booleans.

## 14. No alternate mutation language

Do not create parallel authoritative write paths such as:

```text
create_plan
update_plan
patch_plan
write_fact
save_state
```

A convenience skill may present natural verbs to an LLM, but it must deterministically compile the semantic action to `kawa.emit` and must not bypass the same authentication, authorization, scope, basis, approval, and Event schema checks.

## 15. Fact writes

There is no ordinary `write_fact` or `rewrite_fact` operation.

Facts are projections from accountable Events/evidence.

Use the semantically honest input path:

```text
deterministic measurement → observation.recorded
Human/Agent inference     → claim.recorded
execution outcome         → result.recorded
correction                → new typed Event + corrects/supersedes link
```

Reducers reconstruct Fact state.

A convenience Skill that says “update Fact” MUST resolve that intent to one of these accountable evidence operations; it MUST NOT mutate Fact projection storage.

## 16. Context budget

```text
bootstrap.must_know <= 10
search.default_results <= 20
preflight.must_know <= 10
```

Large detail is pulled by reference when needed.

## 17. No conversation dependence

A model with no prior conversation history should be able to continue through:

```text
kawa.bootstrap
→ kawa.work.next
→ kawa.get as needed
→ kawa.emit semantic output
```

Conversation is disposable. Kawa state is not.

## 18. Mechanical errors become semantic errors

Examples:

```text
CAS failure             → plan_changed/state_changed
lease conflict          → work_already_claimed
projection lag          → state_temporarily_unavailable
approval fingerprint mismatch → approval_stale
```

Ordinary LLM callers should not need the mechanical cause.

## 19. Acceptance tests

```text
A new LLM can orient with an empty bootstrap call when scope is unique.
A new LLM can continue Work without inventing IDs or revision numbers.
A new Problem reference is generated by Kawa.
A Plan revision can be emitted with only the semantic fields not implied by Work.
An LLM inference is emitted as Claim, not deterministic Observation.
A Claim can influence Fact only through Reducer/policy.
The MCP schema exposes Event-specific required fields rather than arbitrary JSON.
The caller cannot supply trusted identity/provenance fields.
The caller does not choose Event schema_version in normal operation.
Two plausible scopes stop with needs_selection.
A copied JWT without proof-of-possession cannot reach semantic processing in the production profile.
A stale state-dependent emit returns semantic conflict.
No direct Fact rewrite path exists.
```

## 20. Core rule

> **Orient. Read. Search. Emit. Continue — with the smallest semantic input that cannot be derived safely.**
