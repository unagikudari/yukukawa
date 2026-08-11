# Kawa User Value and Onboarding v0.1

Status: Current product-positioning and onboarding design note

## 1. Start with what Kawa lets a user do

Kawa's internal architecture deliberately goes deep on Event identity, authority, evidence, causality, current understanding, partitioned history, and replaceable actors.

Those are implementation and correctness concerns. They are not the first thing a user should have to understand.

The user-facing promise is simpler:

> **Change the agent. Keep the work.**

Kawa lets Humans and AI Agents continue shared work without depending on one conversation, one model, one runtime, or one machine.

It preserves what happened, why decisions were made, what evidence supported them, who was allowed to act, and what actually resulted — so another Human or Agent can safely continue.

## 2. Architecture translated into user value

| Kawa internal principle | What the user gets |
|---|---|
| Events are the Domain Source of Truth | What happened is not silently overwritten or lost |
| Current understanding is rebuilt | A new Human/Agent can quickly reconstruct the current situation |
| Preserve divergent history | Work can continue through partitions; conflicts are reconciled later instead of erased |
| Agents are replaceable | Claude, Codex, Gemini, a local model, or a Human can take over the same work |
| Work waits for evidence, not agents | Agents collaborate through durable shared state instead of fragile chat handoffs |
| Claim != Fact | An AI inference is not silently promoted to organizational truth |
| Identity != Authority | A valid login does not automatically permit consequential action |
| Intent != Execution != Result | Saying "I did it" is not proof that the external effect occurred |
| Semantic Retrieval is inspectable | Operators can see what organizational memory an LLM retrieved before acting |
| Local rebuildable projections | The Console stays fast without becoming a second Source of Truth |

## 3. Concrete story

A useful first explanation of Kawa is not an ontology. It is a handoff:

```text
Claude implements a change
        ↓
Kawa records the Plan, Work, evidence, and Result
        ↓
Codex reviews it
        ↓
Gemini is asked to attack the assumptions
        ↓
Claude disappears or the runtime is replaced
        ↓
a different Agent connects to Kawa
        ↓
it pulls the current situation and continues the available Work
        ↓
the operator can inspect why the decision exists and what actually happened
```

No Agent needs the original chat transcript to become the next participant.

## 4. Product explanation order

Public documentation and the first-run experience should follow this order:

```text
Problem the user recognizes
        ↓
What Kawa lets them do
        ↓
Concrete workflow
        ↓
Live Console proof
        ↓
Why it stays reliable
        ↓
Architecture and philosophy
```

Do not lead a new user with event sourcing, epistemology, CAP, authority algebra, or identity theory unless they explicitly want the architecture.

The philosophy should explain why the product remains correct after the user already understands why it is useful.

## 5. First-run onboarding goal

After starting Kawa, a user should be taken to the Operator Console and be able to understand the system without reading the architecture documents first.

The Console should provide a replayable guided tour available from a persistent `?` / Help control.

The tour is an overlay layer over the real Console. It must not be a separate mock UI.

### Interaction model

For each step:

```text
- dim the rest of the page
- highlight the real UI element
- show an arrow pointing to it
- attach a short callout explaining why the user cares
- provide Next / Back / Skip
```

The same tour can be reopened at any time from the Help button.

Do not permanently store tutorial completion as Domain truth. At most it is local/user preference state.

## 6. Suggested first-run tour

### Step 1 — Situation

Highlight the Situation summary.

Callout:

> **This is what Kawa currently understands.**
> See active Problems, Plans, Findings, and meaningful changes without replaying the full history yourself.

### Step 2 — Plan / Work

Highlight the active Plan and available Work.

Callout:

> **This is what needs to happen next.**
> Plans preserve intent. Work exposes actionable steps to Humans and Agents.

### Step 3 — Evidence / Result

Highlight Result / Evidence linkage.

Callout:

> **Kawa separates intention from reality.**
> A task is not proven complete because an Agent says it ran. Results and evidence record what actually happened.

### Step 4 — Decision Lineage

Highlight the lineage/graph control.

Callout:

> **See why this decision exists.**
> Trace evidence and Problems into Plans, Work, Results, Findings, and revisions.

### Step 5 — Runtime

Highlight Runtime / Agent execution state.

Callout:

> **Agents are participants, not the memory.**
> Replace a runtime or model and the next participant can reconstruct the work from Kawa.

### Step 6 — Authority

When implemented, highlight Authority standing separately from execution state.

Callout:

> **Being authenticated is not permission to act.**
> Kawa keeps identity, capability, approval, and execution authority separate.

### Step 7 — Fleet / Replication

When implemented, highlight Node continuity and frontier state.

Callout:

> **Nodes may disconnect without rewriting history.**
> Kawa preserves each branch and reconciles understanding when Nodes reconnect.

### Step 8 — Semantic Retrieval

When implemented, highlight retrieval analysis.

Callout:

> **See what the LLM looked at before it acted.**
> Relevance, freshness, epistemic standing, and selection remain separate so similarity is not mistaken for truth.

## 7. Context-sensitive tour

The tour should be capability-aware.

If a feature is not implemented or not available in the running profile, do not show a fake step. Skip it and explain only what exists.

Examples:

```text
single-node Phase 3
  Situation → Plan/Work → Evidence → Lineage → Runtime

multi-node Phase 4
  + Fleet / Replication

Authority-enabled Phase 5
  + Authority

Semantic Retrieval Phase 6
  + Retrieval analysis
```

This keeps the onboarding honest as the product evolves.

## 8. Tutorial design constraints

```text
[ ] overlay targets real DOM/UI elements
[ ] no mock/synthetic dashboard is required
[ ] tour can be reopened with one Help/? action
[ ] Skip exits immediately
[ ] Back/Next are deterministic
[ ] keyboard navigation works
[ ] focus remains accessible
[ ] callouts remain concise
[ ] feature-absent steps are skipped, not fabricated
[ ] tutorial state is not Domain Source of Truth
[ ] public-demo/screenshot sanitization applies to highlighted content too
```

The tutorial itself must not leak raw PII, secrets, internal URLs, hostnames, or sensitive Artifact content through tooltips or callouts.

## 9. README/public positioning rule

The first public screen should answer:

```text
Why would I use this?
What can it do for me?
Can I see it doing that?
```

Only then should it answer:

```text
How is this possible?
What are the architectural invariants?
```

Recommended short positioning:

> **Change the agent. Keep the work.**
>
> Kawa preserves the evidence, decisions, authority, and Results behind Human/AI work so another participant can safely continue without inheriting the original conversation or machine.

## 10. Core product rule

> **The philosophy is not the product pitch. The philosophy is why the product can keep its promise.**

> **Show continuity first. Explain metaphysics second.**
