# Kawa Git Plan Workspace v0.1

Status: Draft, normative integration rule
Scope: Use Git natively for Plans whose work product is naturally represented as a versioned file tree.

## 1. Principle

Kawa SHOULD use Git directly when a Plan naturally produces or modifies versioned artifacts such as:

```text
source code
documentation
configuration
infrastructure-as-code
schemas
policies
queries
scripts
tests
runbooks
```

Kawa MUST NOT reimplement Git history, diff, branching, merge, or review mechanics inside the Domain Event model merely to manage these artifacts.

> **If the work is naturally a repository, let Git be Git.**

## 2. Source-of-truth boundary

Kawa and Git own different truths.

```text
Kawa
  = organizational intent, evidence, Problem, Plan, Review, Approval,
    Work state, Result, provenance, causality, authorization context

Git
  = versioned file content, commits, trees, branches, diffs, merge history
```

Therefore:

> **Kawa is the Source of Truth for why and under what authority work exists. Git is the Source of Truth for the versioned artifact content produced by that work.**

This is not a competing dual Source of Truth because the semantic domains do not overlap.

Kawa MUST NOT copy an entire repository into Domain Events.

Git MUST NOT become the authoritative store for Kawa Plan state merely because a Plan has a repository.

## 3. Git-capable Plan

A Plan is Git-capable when its intended work can be represented cleanly as changes to a file tree.

Examples:

```text
implement a service
change a database schema definition
write a specification
modify deployment configuration
create tests
update infrastructure-as-code
produce a versioned report or runbook
```

Examples that are not inherently Git-capable:

```text
contain a live incident
restart a service
approve an operation
investigate volatile telemetry
rotate a secret
perform a one-time external action
```

A Plan MAY combine Git work with non-Git actions. Git is used only for the artifact portion.

## 4. Workspace binding

Kawa MAY bind a Plan to a Git workspace.

The workspace may be:

```text
an existing repository
an existing repository plus a Plan-specific branch
a newly provisioned repository
```

The exact Git hosting provider is replaceable.

Kawa should expose the semantic concept `workspace` or `repository`, while provider-specific mechanics remain in an Adapter.

Example conceptual state:

```text
Plan
  addresses -> Problem
  works_in  -> Repository
```

A repository handle is a Resource reference. It is not an authorization token.

## 5. Prefer ordinary Git

Once a Git workspace exists, normal Git concepts SHOULD remain normal Git concepts:

```text
repository
branch
commit
diff
merge
pull request / merge request
tag
```

Kawa SHOULD NOT invent parallel concepts such as:

```text
kawa_commit
plan_diff
kawa_branch
artifact_revision_graph
```

unless a materially different Kawa semantic need is proven.

This follows the same rule as the rest of Kawa:

> **One obvious way to do it.**

## 6. What Kawa records

Kawa records only organizationally meaningful facts about Git activity.

Examples:

```text
Plan began work in Repository R.
A Result references commit C.
A Review evaluated pull request P.
Verification used commit C.
A Plan Result was produced from commit C.
```

These SHOULD normally be represented through existing Events and semantic references rather than a large new Git-specific Domain Event family.

For example:

```text
result.recorded
  result_of -> Plan
  produced  -> git commit resource
```

or:

```text
review.completed
  reviews -> Plan
  based_on -> pull request resource
```

Git provider webhook/audit traffic is not automatically a Domain Event.

## 7. Do not duplicate repository content

The following belong in Git, not Kawa Domain payloads:

```text
full file content
patch bodies
repository trees
commit objects
large diffs
Git pack data
branch histories
merge bases
```

Kawa references them through opaque/safe Resource or Artifact handles.

A Kawa Result may contain a short semantic summary plus references to the exact Git commit or review artifact.

## 8. Plan-to-Git lifecycle

A common path is:

```text
Problem
→ Plan proposed
→ Plan becomes executable
→ Git workspace resolved or provisioned
→ worker edits repository
→ commit / branch / PR managed by Git
→ independent Review
→ merge or other accepted Git outcome
→ verification
→ result.recorded with exact Git references
→ Plan ended
```

Kawa coordinates the work. Git versions the artifact.

## 9. Repository provisioning

When no repository exists and the Plan clearly produces a reusable versioned artifact, Kawa MAY provision one through a Git Adapter.

Provisioning MUST be policy-controlled and capability-gated.

Conceptually:

```text
authenticated Workload
+ repository.create capability
+ approved namespace/owner
+ Plan scope
→ Git Adapter
→ Repository Resource
```

An Agent MUST NOT receive provider credentials merely to create a repository.

Provider credentials remain inside trusted infrastructure.

Repository creation is an external side effect and therefore follows the Security Model's:

```text
Intent
→ Authorization
→ Execution
→ Observation / Result
```

rule.

## 10. Existing repository preference

Kawa SHOULD prefer an existing appropriate repository over creating a new one.

The system should avoid repository proliferation.

A new repository is justified when the work has an independent artifact lifecycle or ownership boundary that cannot be represented cleanly in an existing repository.

## 11. Branches are Git mechanics, not Kawa lifecycle

A Plan-specific branch MAY be useful, but Kawa MUST NOT define Plan semantics in terms of Git branch state.

For example:

```text
branch exists      != Plan is active
branch merged      != Plan is complete
branch deleted     != Plan is retired
```

Kawa derives Plan state from Kawa Events, Review, Approval, Result, and policy.

Git branch state is evidence and artifact state, not the Plan state machine.

## 12. Commits are references, not Kawa Events

A Git commit already has a durable identity and content-addressed history.

Kawa SHOULD reference the commit rather than reproduce it.

Example:

```text
kawa://artifact/git/commit/<opaque-handle>
```

The public identifier format may evolve, but the reference MUST resolve to an exact immutable Git object or provider-independent equivalent.

A commit hash alone SHOULD NOT imply authorization or expose a private repository locator.

## 13. Pull requests and Kawa Review

A Git pull request and a Kawa Review overlap but are not identical.

```text
Git PR/MR
  = artifact change proposal and code/document review surface

Kawa Review
  = independent semantic challenge of the Plan and its evidence, scope,
    safety, architecture, failure modes, and verification
```

A Kawa Review MAY use a Git PR as evidence and review surface.

Kawa SHOULD NOT assume that "PR approved" automatically satisfies every Kawa Review or Human Approval requirement.

Policy may explicitly map certain repository review rules to Kawa requirements when the assurance is equivalent.

## 14. Exact-version binding

When a Plan, Review, Approval, verification, or Result depends on repository content, it SHOULD bind to an immutable Git version, normally a commit.

Avoid binding security-sensitive decisions only to mutable names such as:

```text
main
latest
feature/current
```

The LLM-facing interface may display the friendly branch/PR name, while Kawa internally preserves the exact immutable reference used for accountability.

## 15. Conflict model

Git conflicts remain Git conflicts.

Kawa MUST NOT duplicate line-level merge-conflict resolution logic.

Kawa may surface a semantic state such as:

```text
status: conflict
reason: repository_change_conflict
next_allowed_actions:
  - inspect_repository
  - resolve_conflict
  - revise_plan
```

The actual file merge is performed through Git tooling.

If the conflict changes Plan meaning, scope, risk, or evidence, Kawa requires Plan revalidation/review as appropriate.

## 16. Security boundary

All Git integration follows `docs/security-model-v0.1.md`.

In particular:

```text
Agent does not receive raw Git provider credentials.
Repository handles carry no authority.
Authorization precedes repository discovery.
Private repository location is disclosed only when allowed.
Repository creation/push/merge are capability-gated external actions.
Human Approval remains distinct from natural-language or PR comments.
```

Git hosting architecture may be public. Live credentials, private repository mappings, capability bindings, and operational weakness remain private.

## 17. LLM-friendly behavior

The ordinary LLM should need only semantic instructions such as:

```text
Continue this Plan.
Work in the attached repository.
Review the proposed change.
Verify the current commit.
```

It should not need Kawa-specific repository synchronization commands or a duplicate version-control vocabulary.

Kawa provides the current repository/workspace reference and permitted next actions.

Git-capable coding agents can then use their existing Git skills directly.

## 18. Minimal integration surface

The initial Kawa Core does not need a large Git API.

A thin Adapter can support semantic operations such as:

```text
resolve repository
provision repository
prepare workspace
reference commit
reference pull request
observe accepted/merged result
```

Provider-specific details belong behind the Adapter.

If an MCP client already has safe Git tools, Kawa MAY provide only the authorized repository reference and expected Plan context rather than proxy every Git operation.

## 19. Recovery

If an Agent dies:

```text
Kawa Plan remains
Git repository remains
commits remain
Work claim expires/reconciles
next Agent receives current Plan + repository reference + exact version
```

No conversation handoff is necessary.

This is one of the strongest reasons to use Git natively for Git-shaped work.

## 20. Ten-year test

This design passes only if:

```text
Can Kawa change Git hosting providers without changing Plan semantics?
Can Git remain usable without Kawa-specific commit formats?
Can Kawa recover current Plan state without parsing commit-message conventions?
Can repository history remain ordinary Git history?
Can a future Agent continue from an exact repository version without prior conversation?
Can Kawa avoid storing duplicate file history?
```

## 21. Core rule

> **Kawa manages why the work exists and what it means. Git manages the versioned artifact produced by the work.**

Or more compactly:

> **Plans in Kawa. Artifacts in Git. References between them.**
