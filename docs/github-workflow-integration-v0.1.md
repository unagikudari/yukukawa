# Kawa GitHub Workflow Integration v0.1

Status: Draft, normative boundary
Scope: Responsibility split between Kawa semantic workflow and GitHub repository workflow mechanics.

## 1. Core rule

> **GitHub can execute workflow mechanics. Kawa owns the meaning of the workflow.**

Kawa MUST NOT become a duplicate GitHub workflow engine.

GitHub SHOULD be used for mechanics that are native to Git repositories and pull-request workflows.

Kawa SHOULD retain semantic state about why work exists, what Plan is current, what Review or Approval is required, and what organizational Result followed.

## 2. Responsibility split

```text
Kawa semantic workflow
  Problem
  Plan
  Review
  Approval
  Result

Trusted Git Adapter
  maps authorized semantic intent to GitHub operations
  maps verified GitHub facts back to Kawa Observations / Results

GitHub mechanics
  repository
  branch
  commit
  pull request
  code review
  status check
  Actions workflow
  ruleset / branch protection
  merge queue
  webhook
```

The Adapter is a boundary, not a second Source of Truth.

## 3. GitHub-native mechanics

The following SHOULD normally remain GitHub-native when a Plan has a Git workspace:

```text
repository creation
branch creation
commit storage
pull-request lifecycle
review requests
required reviewers
status checks
CI execution
merge rules
merge queue
workflow dispatch
webhooks
```

Kawa SHOULD reference exact Git objects where they matter, rather than copying their full content into Domain state.

## 4. Kawa-native semantics

The following remain Kawa concepts even when GitHub participates in execution:

```text
why the work exists
which Problem the Plan addresses
current Plan rationale
required adversarial/security/operational Review
open Findings
whether Kawa policy requires Human Approval
what organizational Result occurred
what evidence supports current understanding
```

GitHub state MUST NOT silently redefine these meanings.

## 5. Trigger direction: GitHub to Kawa

GitHub-originated changes SHOULD enter Kawa through authenticated webhook or equivalent trusted event delivery.

Conceptually:

```text
GitHub event
→ authenticated webhook receiver
→ verify event origin and delivery integrity
→ Git Adapter
→ normalize deterministic fact
→ observation.recorded / result.recorded
```

Examples:

```text
pull request opened
pull request synchronized
review submitted
status check completed
workflow completed
merge completed
```

The Adapter MUST attach trusted provenance from the verified execution path. An LLM MUST NOT self-assert that a GitHub event occurred.

## 6. Trigger direction: Kawa to GitHub

Kawa MAY request GitHub operations when an authenticated workload has the required capability and policy allows the action.

Conceptually:

```text
Kawa semantic intent
→ authorization / approval if required
→ trusted Git Adapter
→ GitHub API / workflow dispatch
→ GitHub result
→ deterministic observation/result back into Kawa
```

Examples:

```text
create repository
create branch
open pull request
request reviewers
start approved workflow
enable or update repository rules where policy permits
```

Agents SHOULD NOT receive raw GitHub credentials for normal operation.

## 7. GitHub Review is not automatically Kawa Review

GitHub pull-request review and Kawa Review are distinct concepts.

```text
GitHub Review
= review of a Git artifact/change set

Kawa Review
= semantic evaluation of a Plan, such as adversarial, security, schema, or operational review
```

A GitHub review MAY satisfy a Kawa Review requirement only when Kawa policy explicitly defines that mapping and the exact reviewed artifact/scope is bound to the relevant Plan.

Otherwise:

```text
GitHub PR approved != Kawa review.completed
```

## 8. GitHub Approval is not automatically Kawa Human Approval

Repository review approval, environment approval, and Kawa Human Approval are separate authority concepts.

A GitHub approval MAY participate in an approval policy only if Kawa can verify that it binds the required:

```text
Human Principal
Plan / Plan revision
resource or artifact scope
operation set
expiry or validity window when required
```

By default:

```text
GitHub PR approved != Kawa approval.granted
```

High-risk Kawa Approval remains governed by `docs/security-model-v0.1.md`.

## 9. Git state is not Plan state

Git lifecycle transitions MUST NOT be treated as Plan lifecycle transitions without explicit semantic rules.

Examples:

```text
branch merged   != Plan completed
branch deleted  != Plan ended
PR closed       != Problem resolved
CI passed       != Plan safe
workflow success != organizational success
```

They are evidence or execution facts from which Kawa may derive or request further semantic decisions.

## 10. Deterministic ingestion

GitHub facts are machine-observed state and SHOULD use the deterministic Observation ingestion rules.

For example:

```text
subject_ref        = Git resource handle
observer_ref       = authenticated Git Adapter workload
observation_method = github.webhook.pull_request
predicate          = git.pull_request.state
value_text         = open
```

The concrete observation method value is attached by the trusted Adapter, not provided by LLM text.

See `docs/deterministic-observation-ingestion-v0.1.md`.

## 11. Avoid duplicated workflow state

Kawa MUST NOT recreate GitHub-native mechanics as durable Domain concepts merely to mirror GitHub.

Avoid Domain objects such as:

```text
KawaBranch
KawaCommit
KawaPullRequest
KawaCheckRun
KawaMergeQueue
```

unless future evidence proves that a Git-independent semantic concept is actually required.

Use opaque Resource / artifact references and deterministic observations instead.

## 12. Failure and reconciliation

Outbound GitHub actions are external side effects and MUST follow Kawa's side-effect rules.

```text
Intent
→ Authorization
→ Execution
→ Observation / Result
```

Timeouts and ambiguous GitHub outcomes MUST be reconciled against GitHub before retrying an operation that may not be idempotent.

Event replay MUST NOT recreate repositories, reopen pull requests, re-run workflows, or re-merge branches.

## 13. Security boundary

The Git Adapter is a trusted mediator.

It MUST enforce:

```text
authenticated caller
explicit capability
resource scope
operation scope
Kawa Approval when required
credential isolation
verified GitHub webhook provenance
```

GitHub tokens, app private keys, webhook secrets, and installation mappings belong to the Security plane and MUST NOT be written into Domain Events.

## 14. Design test

For any proposed GitHub integration, ask:

```text
Is this a Git-native mechanic?
  yes -> GitHub should normally own it

Does this express organizational meaning?
  yes -> Kawa should normally own it

Is information crossing the boundary?
  yes -> use a trusted Adapter and explicit references/Observations

Would implementing this in Kawa duplicate GitHub?
  yes -> do not implement it in Kawa
```

## 15. Core shape

```text
Kawa says WHAT and WHY.
GitHub handles Git-native HOW.
The Adapter translates verified facts and authorized intent.
```

> **Semantic workflow in Kawa. Repository workflow in GitHub. One trusted boundary between them.**
