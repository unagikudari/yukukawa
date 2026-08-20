# Publication Boundary

Status: Normative repository hygiene rule

Kawa is intended to be publishable. Repository content MUST be portable and MUST NOT disclose operator-specific infrastructure, credentials, identities, or local environment details.

## Public-by-design security

Kawa's security architecture, protocols, schemas, trust boundaries, and normative security model are intended to be safe to publish.

> **Kawa MUST remain secure when an attacker can read the security design.**

Security MUST depend on protected keys, authenticated identity, explicit authorization, scoped capabilities, cryptographic approval, mediated execution, and enforced boundaries—not on obscurity of architecture.

The publication boundary is:

```text
Security architecture / invariants → public
Secrets / live security state       → private
Operational weakness / exceptions   → private
```

Safe-to-publish material includes the existence and design of controls such as:

- Human / Node / Workload identity separation
- TPM-backed Node trust principles
- JWT validation requirements
- authorization-before-disclosure
- capability and Resource Handle separation
- Wizard only after successful authentication
- Human approval binding
- Secret Broker / mediated execution
- break-glass design principles
- external side-effect separation and reconciliation
- revocation and containment mechanisms

Publishing these controls MUST NOT be treated as weakening a conforming Kawa deployment.

See `docs/security-model-v0.1.md` for the normative security model.

## MUST NOT commit

- `.env` files or secret-bearing environment configuration
- API keys, access tokens, passwords, private keys, certificates containing private material, recovery codes, or session credentials
- JWT signing keys or actual JWTs
- live Node / Workload identities or current capability bindings
- real hostnames, internal DNS names, private IP addresses, VPN details, local usernames, home-directory paths, mount paths, or device serial numbers
- personal or organization-specific hardware inventories unless intentionally published as anonymized test data
- exact local machine models, GPU/CPU inventories, storage layouts, network topology, or Node identifiers taken from an operator environment
- production database connection strings, resource locators, Resource Handle-to-locator mappings, Secret Broker mappings, or capability bindings
- approval proof material or current revocation state
- unpatched weaknesses, operational security exceptions, defensive gaps, or deployment-specific bypasses
- raw logs, dumps, telemetry, incident evidence, or screenshots copied from a real environment unless explicitly sanitized and approved for publication
- security logs containing exploitable operational detail

## Examples and fixtures

Documentation examples, tests, fixtures, and sample inventories MUST use synthetic data. Values MUST NOT be copied from an operator's real environment merely because they appear harmless.

Prefer placeholders or clearly fictional values:

```text
nod_example_a
wrk_example_collector
res_example_database
hardware.ram.total_bytes = <bytes>
hardware.gpu.vendor = <vendor>
example.invalid
192.0.2.0/24
198.51.100.0/24
203.0.113.0/24
```

The RFC 5737 IPv4 documentation ranges above are preferred when an IP address is necessary in examples.

## Dogfood evidence exception (bare nicknames, real measurements)

Kawa's own doctrine requires fixtures and evaluation corpora for judgment logic to come from **real measurements**, with honest provenance labels — synthetic relabeling would falsify the provenance the artifact exists to preserve. Where the synthetic-data rule above and that doctrine collide, the following narrow exception applies:

Dogfood-derived fixtures, corpora, and measurement records MAY retain, intentionally:

- **bare node/agent nicknames** (e.g. a node short name or an agent lane label) used as provenance attribution, and
- **real measured values** (lags, counts, timings, event shapes) from the operator's dogfood deployment,

provided that they carry **no network coordinates, hostnames/DNS names, IP addresses, filesystem paths, credentials, or capability bindings** — every other MUST NOT above still applies, and the retention is a deliberate provenance decision, not an oversight.

Consequently, **purging historical nicknames via Git history rewrite is prohibited**: commit identity is load-bearing for Kawa provenance (Events, Results, and review verdicts pin commit hashes; multiple nodes hold checkouts). A finding against a bare nickname is resolved by this exception or by a forward-only change, never by rewriting published history.

## Private coordinates: coordinate yes, link no

The dogfood exception above keeps private *coordinates* in the published tree on purpose — publication condition 7 has internal Events pinning private commit SHAs, and evaluation corpora cite the planning issues they were derived from. That retention is deliberate and stays.

An **actionable coordinate** into the private repository is a different thing, and is prohibited: it is a 404 for every reader of the published tree, and it promises a destination the boundary exists to withhold. The distinction is whether the reader is invited to *act* — follow it, or clone it — not whether the private name appears.

- `github:<owner>/<repo>#122` as a provenance coordinate — **allowed** (nothing renders it, nothing clones it)
- `https://github.com/<owner>/<private-repo>/issues/122` — **prohibited**
- `git@github.com:<owner>/<private-repo>.git` — **prohibited** (a clone instruction the reader cannot carry out is the same broken promise in different syntax)

**This distinction is conditional, not a property of the coordinate syntax.** It holds only while nothing downstream turns `github:<owner>/<repo>#N` into an href. If a renderer ever auto-links that form, the coordinate becomes a broken promise too and the rule must widen to cover it. Re-check this whenever the publication surface gains a new renderer.

Mechanized as the `private-repo` rule in `scripts/lint_publication_boundary.py`, scoped to this project's own GitHub namespace: a link to a third party's repository is ordinary, while a link into our own namespace is either the public projection or something a reader cannot open. Scoping it that way needs no maintenance when a new private repository appears.

Found by inspection on 2026-08-20, not by the gate: `github.com` is an allowlisted **host**, so every existing rule waved through a URL whose **path** named the private repo — and one such link had already shipped to the live mirror.

Retire or re-scope this rule when **either**: the dev repository is published (nothing left to protect), **or** the project needs more than one development namespace — the exact-owner match assumes exactly one.

## GitHub metadata plane

Changing repository visibility publishes more than the tree: **Issues, Pull Requests, review comments, and their full edit histories** become public at the same moment, and edits do not remove prior revisions from public view. Forks and caches make this exposure irreversible.

Therefore:

- the GitHub discussion plane is part of the publication surface and MUST be explicitly dispositioned before any visibility change (publish-in-place accepted, or a clean mirror repository chosen);
- while publication is pending, new Issue/PR content SHOULD avoid operational identifiers (host/lane names, local paths, coordination-system task ids) — refer to work by commit hash, document name, and role (e.g. "round-2 reviewer") instead, so the discussion corpus stops accumulating operator detail;
- redaction-by-edit MUST NOT be relied on as a pre-publication cleanup mechanism.

## Local configuration pattern

Public repository:

```text
config.example.yaml
.env.example
inventory.example.yaml
```

Operator environment, ignored by Git:

```text
.env
config.local.yaml
inventory.local.yaml
secrets/
private/
```

Example files MUST contain placeholders only and MUST be safe to publish unchanged.

## Design rule

Kawa Events may describe real local hardware and infrastructure at runtime. That runtime Situation Awareness belongs in the deployed Kawa instance, not in the source repository.

The source repository defines schemas, protocols, security invariants, synthetic examples, tests, and implementation. It does not serve as a backup of an operator's Situation Awareness or live security state.

## Review gate

Before a branch is merged or made public, review SHOULD check for:

1. secrets and credentials,
2. environment-specific paths and addresses,
3. real hardware or infrastructure identifiers,
4. live identity/capability/resource mappings,
5. approval/revocation/security state,
6. operational weaknesses or deployment-specific exceptions,
7. logs/dumps/fixtures derived from real deployments,
8. examples that accidentally encode operator-specific facts.

When uncertain, replace the value with a synthetic placeholder rather than publishing it.
