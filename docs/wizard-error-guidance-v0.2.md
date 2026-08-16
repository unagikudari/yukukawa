# Kawa Wizard Error Guidance v0.2

Status: Draft
Scope: Authenticated LLM-facing guidance for conflicts, missing preconditions, approvals, capabilities, and recoverable errors.

This version strengthens the authentication boundary from v0.1.

## 1. Core rule

Wizard guidance is not available to an unauthenticated caller.

> **No valid JWT, no Wizard.**

Kawa MUST complete JWT verification before it evaluates or exposes Wizard guidance.

A caller with an invalid, expired, not-yet-valid, incorrectly issued, incorrectly scoped, or otherwise unacceptable JWT receives only a minimal authentication failure.

The Wizard begins only after Kawa has established an authenticated principal from the JWT.

## 2. Processing order

The processing order is security-critical and MUST remain explicit:

```text
request
→ parse only what is necessary for authentication
→ verify JWT
    invalid → minimal authentication rejection
    valid   → authenticated principal
→ authorize requested scope/action
→ validate semantic request
→ evaluate current state / policy
→ success OR typed Wizard response
```

Wizard, schema discovery, capability guidance, Project discovery, Plan discovery, and current-state inspection MUST NOT run before successful JWT verification.

## 3. JWT acceptance

The exact JWT profile may evolve, but an accepted token MUST satisfy the configured authentication policy.

At minimum, validation normally includes:

```text
signature valid
algorithm allowed
issuer accepted
audience accepted
expiration valid
not-before valid when present
required claims present
principal/workload identity resolvable
token not revoked when revocation is supported
```

Kawa MUST NOT treat successful decoding as successful authentication.

A JWT is accepted only after cryptographic and semantic validation.

## 4. Minimal rejection for invalid JWT

Invalid authentication MUST NOT become a guidance oracle.

Example:

```text
HTTP 401 Unauthorized
```

A minimal response MAY identify the broad authentication class needed for interoperability, but MUST NOT reveal:

```text
Projects
Plans
Problems
Resources
capability names granted to other callers
required approvals
current workflow state
matching object identifiers
internal topology
policy details
next_allowed_actions for Kawa domain operations
```

The response MUST NOT say things such as:

```text
Your token is invalid, but Plan pln_123 exists and requires plan.review.
```

or:

```text
Authenticate as workload X to access Resource Y.
```

Authentication failure is a boundary, not a Wizard branch.

## 5. Valid JWT establishes the Wizard boundary

After successful JWT verification, Kawa resolves the authenticated principal and its authorization context.

Conceptually:

```text
valid JWT
→ principal
→ scope / capability / policy
→ Wizard-visible state
```

Wizard output MUST then be filtered by that authorization context.

A valid JWT does not imply universal visibility.

## 6. 401, 403, and 409 have different meanings

Kawa SHOULD preserve the semantic distinction:

```text
401 Unauthorized
  identity is not authenticated
  → no Wizard

403 Forbidden
  identity is authenticated, action is not authorized
  → limited authorized guidance MAY be returned

409 Conflict
  identity and requested action are understood, but current state conflicts
  → Wizard SHOULD guide the caller toward valid next actions
```

This prevents authentication, authorization, and state conflict from collapsing into one ambiguous error path.

## 7. 403 guidance after valid JWT

A valid JWT may reach a `403 Forbidden` response when the principal lacks authority for the requested operation.

Wizard guidance MAY explain only what the authenticated caller is authorized to know.

Example:

```yaml
status: capability_required
reason: action_not_authorized
message: This workload is not authorized to perform the requested action.

next_allowed_actions:
  - action: inspect_plan
    target: kawa://plan/pln_example
```

Only include `inspect_plan` if that exact caller may inspect that Plan.

Do not reveal hidden capabilities, resources, approvers, or alternate principals merely to make the error more helpful.

## 8. 409 as authenticated Wizard entry point

`409 Conflict` is a primary Wizard entry point only after authentication and authorization context are established.

Examples:

```text
plan_changed
approval_stale
work_already_claimed
problem_reframed
fact_conflicted
unresolved_finding
```

Example:

```yaml
status: conflict
reason: plan_changed
message: The Plan changed after your last read.

current:
  ref: kawa://plan/pln_example
  state: reviewing

next_allowed_actions:
  - action: get
    target: kawa://plan/pln_example
  - action: revise
    target: kawa://plan/pln_example
```

Every field in the response is subject to authorization filtering for the authenticated principal.

## 9. Wizard is deterministic guidance

Wizard behavior MUST be derived from:

```text
authenticated principal
authorized scope
current state
schema
policy
```

Wizard MUST NOT use an LLM to invent next actions.

The ordering matters:

```text
authentication
→ authorization
→ state
→ guidance
```

Never:

```text
state discovery
→ guidance generation
→ authentication
```

## 10. `next_allowed_actions` is principal-specific

`next_allowed_actions` is authoritative only for the authenticated caller in the current response context.

It MUST NOT be a global list of theoretically possible actions.

Conceptually:

```text
all semantically valid actions
∩ actions visible to principal
∩ actions authorized for principal
∩ actions allowed by current state/policy
=
next_allowed_actions
```

This makes Wizard guidance both LLM-friendly and security-preserving.

## 11. Missing input and selection

`needs_input` and `needs_selection` are available only after JWT verification when the request concerns protected Kawa state.

Example:

```yaml
status: needs_input
reason: missing_objective
message: A Plan needs an objective.
required_input:
  - field: objective
    type: text
    meaning: What should this Plan achieve?
```

For ambiguous object selection, Kawa MUST filter candidates before returning choices.

```text
authorize visibility
→ find candidates within visible scope
→ return choices
```

Never search globally and then filter only after candidate details have already influenced the response.

## 12. Capability guidance must not become enumeration

Capability guidance is especially sensitive.

Kawa MAY say:

```text
The requested action is not authorized for this workload.
```

Kawa MAY name a capability when policy explicitly allows the authenticated caller to discover it.

Kawa MUST NOT expose the capability catalog, privileged resource set, alternate identities, or escalation topology merely because the JWT itself is valid.

Discoverability and authorization remain separate.

## 13. JWT is a mechanism, not Kawa domain semantics

Kawa currently uses verified JWTs as an authentication boundary, but JWT-specific mechanics MUST NOT leak into Domain Events or LLM-facing business semantics.

Do not create Domain concepts such as:

```text
jwt_plan
jwt_problem
jwt_review
```

Domain semantics remain:

```text
Project
Problem
Plan
Review
Work
Result
```

Authentication infrastructure may be replaced in the future without changing those meanings.

The durable rule is:

> **Wizard requires authenticated identity. The current authentication mechanism is a verified JWT.**

## 14. Logging and diagnostics

Detailed JWT validation failures MAY be recorded in protected security logs for operators.

Examples:

```text
signature_invalid
issuer_mismatch
audience_mismatch
expired
not_yet_valid
revoked
required_claim_missing
```

These diagnostic details SHOULD NOT automatically be reflected to an unauthenticated or unauthorized caller.

Operational observability and caller guidance are separate concerns.

## 15. Security acceptance tests

The implementation MUST pass tests equivalent to:

```text
Invalid JWT cannot discover whether a Project exists.
Invalid JWT cannot discover whether a Plan exists.
Invalid JWT cannot receive next_allowed_actions.
Invalid JWT cannot enumerate capabilities.
Invalid JWT cannot learn approval requirements.
Invalid JWT cannot trigger semantic search over protected state.

Valid JWT + no permission cannot see unauthorized state.
Valid JWT + permitted state can receive Wizard guidance.
409 guidance contains only authorized refs/actions.
```

## 16. LLM-friendly rule

LLM-friendly does not mean information-rich before trust is established.

For an authenticated caller:

```text
Explicit semantics.
One obvious path.
Guided recovery.
```

For an unauthenticated caller:

```text
Minimal surface.
No state disclosure.
No guidance oracle.
```

These are complementary, not conflicting, goals.

## 17. Canonical boundary

```text
             ┌─────────────────────────┐
request ────>│ JWT verification        │
             └────────────┬────────────┘
                          │
                invalid   │   valid
                    │     │     │
                    v     │     v
              minimal 401 │  authenticated principal
                          │     │
                          │     v
                          │ authorization
                          │     │
                          │     v
                          │ semantic processing
                          │     │
                          │     v
                          │ success / Wizard
```

> **No valid JWT, no Wizard.**
