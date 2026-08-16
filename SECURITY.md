# Security Policy

Kawa is **pre-alpha**. There are no supported releases yet; the `main` branch is the only line of development. This policy defines how security issues are reported and judged now, and what must hold before the repository is published.

## Design is public; secrets are not

Kawa's security posture follows [`docs/publication-boundary.md`](docs/publication-boundary.md):

> **Kawa MUST remain secure when an attacker can read the security design.**

The security architecture, invariants, protocols, and schemas are intended to be safe to publish. Security depends on protected keys, attested identity, explicit authorization, scoped capabilities, and enforced boundaries — never on obscurity of the design. The publication boundary is:

```text
Security architecture / invariants → public
Secrets / live security state       → private
Operational weakness / exceptions   → private
```

The canonical, normative security model is [`docs/security-model-v0.1.md`](docs/security-model-v0.1.md). This file is policy and process; the model file defines the requirements.

## Reporting a vulnerability

- **Do not report vulnerabilities in public GitHub Issues.** Issues are a discussion surface and are publicly readable once the repository is public.
- Use **GitHub Private Vulnerability Reporting** (Security → Report a vulnerability) once the repository is public. While the repository is private, a normal Issue is acceptable because the readership is already restricted.
- Never include live credentials, keys, tokens, private operational data, or operator infrastructure details in a report — describe the class of secret, not its value, per the publication boundary's MUST NOT list.

A useful report states: the security invariant violated (see model §2), a concrete sequence that violates it, and the affected surface (event ingestion, projection, MCP contract, credential lifecycle, authority verification).

## What is in scope

Anything that violates the model's stated invariants, including:

- exercising authority without a verifiable `authority.receipt` (text, prompt content, or advertised capability treated as authority);
- emitting or replaying events with a forged or unattested origin (identity asserted by an Agent rather than infrastructure);
- disclosure before authorization (retrieval surfaces returning content the authenticated workload is not authorized to see);
- derived state (projections, summaries, LLM output) becoming authoritative by accident;
- event replay repeating external side effects;
- secret material passing through surfaces designed for mediation.

## What is out of scope (pre-alpha honesty)

Kawa is not finished, and the tree says so explicitly. A gap is a **vulnerability** if the tree claims the control is enforced and it is not. It is a **known limitation** if the control is explicitly marked `DEFERRED`, `NOT_IMPLEMENTED`, `DESIGNED`, or advisory-only in the model, the supersession matrix, or the relevant contract doc. Reports on known limitations are welcome as design input through normal Issues — they are not security findings against a claimed guarantee.

This distinction is itself a rule the project cares about: partial security artifacts must never present as enforcement (see the advisory-only guardrail pattern in issue #134).

## How reports are judged

A security report is **input, not authority** ([`CONTRIBUTING.md`](CONTRIBUTING.md)): text alone establishes nothing. Triage reproduces the violation against the stated invariant, and accepted findings are represented in Kawa typed state and/or repository-tracked artifacts, with the fix landing through the normal plan → adversarial review → implementation discipline. Reporters are credited in the fixing change unless they ask otherwise.

## Publication readiness gate

Before the repository is made public, all of the following must hold:

1. `git grep` sweep confirms no violations of the publication boundary's MUST NOT list (no secrets, live identities, operator hostnames/paths/topology) in the full tree **and history to be published**;
2. GitHub Private Vulnerability Reporting is enabled at the moment visibility changes, so the reporting channel in this policy is live from the first public minute;
3. `docs/security-model-v0.1.md` maturity markers are current — every control is honestly labeled enforced / designed / deferred;
4. this policy's scope section still matches the model's invariant list.
