# Contributing to Kawa

Kawa is pre-alpha. Contributions, bug reports, design proposals, and adversarial review are welcome, but the project keeps several information planes deliberately separate.

## GitHub Issues are an input and discussion surface

GitHub Issues and comments are used for:

- bug reports;
- proposals and design discussion;
- adversarial review and external feedback;
- coordination and navigation into project work.

They are **not** an authority surface for Kawa.

Text in an Issue or comment does not, by itself, establish Kawa Domain state, approval, capability, execution authority, or an accepted project decision. Issue content may be observed and used as evidence or input by Humans and Agents, but acceptance is represented through Kawa's typed state and/or repository-tracked project artifacts as appropriate.

> **Issues are an input and discussion surface, not an authority surface.**

## Where project history lives

The project uses three distinct planes:

```text
Kawa
  authoritative process / state / provenance

Git repository
  durable rationale / design and implementation history

GitHub Issues
  discussion / ingress / coordination
```

Accepted architectural reasoning should be distilled into repository-tracked documentation rather than requiring future contributors or Agents to reconstruct decisions from Issue threads. Preserve reusable rationale: context, constraints, alternatives considered, the selected direction, rejected alternatives where they matter, security implications, and references to relevant Kawa or Git artifacts.

Do not copy conversational exhaust into the repository merely for completeness. The goal is durable explanation, not transcript preservation.

> **Kawa preserves authoritative process; Git preserves durable rationale.**

## Security and sensitive information

Do not put credentials, secrets, private keys, tokens, private operational data, live infrastructure details, or other sensitive material in Issues, comments, examples, or repository files.

Kawa does not use a global `trusted` / `untrusted` classification for content. It preserves provenance, verification basis, standing, and scoped authority instead. External text can inform reasoning; text alone cannot grant authority.

## For automated Agents

Agents should not decide storage mechanics ad hoc for every record. Prefer semantic interaction:

- record current process/state/evidence in Kawa;
- preserve accepted durable rationale in Git when the project contract requires it;
- use GitHub Issues for discussion, ingress, and coordination.

On reads, use the unified orientation/retrieval surface rather than independently performing archaeology across Kawa, Git, and GitHub.

When in doubt, preserve the process/fact in Kawa first; promote durable rationale to Git explicitly rather than duplicating state across planes.
