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
