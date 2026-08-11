# Kawa Deterministic Observation Ingestion v0.1

Status: Draft
Scope: Deterministic collection and ingestion of machine-observable facts into Kawa Observation Events.

## 1. Principle

Kawa should prefer deterministic collectors for directly observable machine state.

> **Observe with tools. Reason with LLMs. Do not use an LLM where a deterministic collector can produce the observation.**

Examples include:

```text
hostname
OS name/version
kernel version
CPU model/core count
RAM size
GPU model/vendor/VRAM
filesystem size/free space
installed package versions
service state
open/listening ports when policy permits
configuration values
vulnerability scanner findings
cloud/resource inventory
container/runtime inventory
```

LLM inference is not required to collect these values.

## 2. Separation of responsibilities

```text
Collector / Scanner
    ↓ deterministic output
Adapter / Normalizer
    ↓ typed semantic mapping
Kawa Emit
    ↓
observation.recorded
    ↓
Reducer
    ↓
Facet / Fact / Situation Awareness
```

Kawa does not need to embed Ansible, a vulnerability scanner, or operating-system command logic into Core.

Collectors are replaceable.

Kawa owns the semantic Observation contract.

## 3. Collector examples

Suitable collectors may include:

```text
Ansible facts
hostname / hostnamectl
uname
/proc and /sys readers
systemd/service queries
package manager queries
container runtime APIs
cloud provider inventory APIs
vulnerability scanners
configuration parsers
hardware inventory tools
network inventory tools
```

The exact tool is not part of durable Kawa semantics.

For example, these may all produce the same semantic predicate:

```text
Ansible
hostnamectl
native OS API
custom collector
        ↓
system.hostname
```

The durable meaning is `system.hostname`, not the collector implementation.

## 4. Observation Event shape

A deterministic collector produces a typed Observation Event conceptually like:

```text
event_type   = observation.recorded
subject_ref  = nod_example
observer_ref = wrk_inventory_collector
predicate    = system.hostname
value_text   = node-example
source       = hostnamectl
occurred_at  = collection time
```

Another implementation could emit:

```text
source = ansible.setup
```

while preserving the same predicate.

This allows tools to change without changing Kawa semantics.

## 5. Observer identity

Collector identity is a Workload Identity.

Trusted infrastructure attaches the authoritative `observer_ref`.

The collector MUST NOT self-assert arbitrary trusted identity.

Examples:

```text
wrk_ansible_collector
wrk_vulnerability_scanner
wrk_inventory_agent
wrk_config_collector
```

These are examples only; repository fixtures must remain synthetic.

## 6. Source versus observer

`observer_ref` and `source` have different meanings.

```text
observer_ref = authenticated workload that performed or submitted the observation
source       = deterministic method/tool/data source used to obtain the value
```

Example:

```text
observer_ref = wrk_inventory_collector
source       = hostnamectl
```

or:

```text
observer_ref = wrk_inventory_collector
source       = ansible.setup
```

This distinction preserves accountability while allowing implementation changes.

## 7. Canonical predicates

Predicates should describe the meaning of the value rather than the collector-specific field name.

Prefer:

```text
system.hostname
software.os.name
software.os.version
software.kernel.version
hardware.cpu.model
hardware.cpu.logical_cores
hardware.ram.total_bytes
hardware.gpu.vendor
hardware.gpu.model
hardware.gpu.vram_bytes
storage.root.total_bytes
storage.root.free_bytes
middleware.postgresql.version
middleware.docker.version
config.postgresql.max_connections
```

Avoid importing tool-specific structures directly, for example:

```text
ansible_facts.ansible_hostname
scanner.output.field_17
lshw.node.children[3].product
```

Tool-specific fields belong in adapters, not the durable semantic vocabulary.

## 8. Vulnerability scanner ingestion

A vulnerability scanner is also a deterministic observer when it reports tool-produced findings.

Kawa should preserve the distinction between:

```text
scanner observation
organizational interpretation
```

For example:

```text
Observation:
  predicate = vulnerability.cve.detected
  value      = CVE-20XX-XXXX
  source     = scanner-name
```

or more explicit typed observations such as:

```text
vulnerability.identifier
vulnerability.detected
vulnerability.cvss.base_score
vulnerability.package.name
vulnerability.package.version
```

The scanner saying a vulnerability is present is evidence.

Whether it is currently exploitable, operationally important, accepted risk, or a Problem requiring action may be derived or decided separately.

Do not collapse scanner output directly into organizational truth without policy/provenance.

## 9. Collector classes

Collectors may be classified operationally by expected change rate:

```text
static
slow_changing
dynamic
```

Examples:

```text
static:
  hardware model
  CPU model

slow_changing:
  OS version
  installed package version
  hostname
  configuration

 dynamic:
  free disk space
  service state
  vulnerability state
```

This classification affects scheduling, not durable Event semantics.

## 10. Collection strategy

Kawa SHOULD avoid emitting meaningless duplicate observations when the domain does not require every sample.

Possible policies:

```text
emit on change
emit periodically
emit on threshold crossing
emit on scanner finding change
emit explicit aggregate
```

High-frequency telemetry should normally remain in telemetry systems such as metrics/log platforms.

Kawa records observations that materially contribute to Situation Awareness.

## 11. Raw output handling

Raw collector output SHOULD NOT be placed wholesale into the Domain Event payload.

Prefer:

```text
raw tool output
    ↓ adapter
explicit typed observations
```

If retaining the raw source is useful for audit or forensic purposes, store it as an external artifact and link it to the Observation Event.

This preserves both:

```text
machine-readable semantic value
raw evidentiary source
```

without turning the Event store into a generic document store.

## 12. Adapter contract

Each deterministic collector adapter should define:

```text
input source/tool
supported predicates
value types
unit normalization
subject resolution
collection timestamp semantics
error behavior
raw artifact policy
```

The adapter SHOULD be deterministic for the same collector output and mapping version.

An LLM MUST NOT be required to translate routine collector fields into canonical predicates on the hot path.

## 13. Unknown and failed collection

Collection failure is not equivalent to a negative observation.

For example:

```text
could not determine hostname
```

must not become:

```text
system.hostname = none
```

Possible handling includes:

```text
no Observation Event
collector operational Result/Event
explicit unknown observation only when the domain requires it
```

Absence of evidence is not evidence of absence.

## 14. Security boundary

Collectors are authenticated workloads and remain subject to the Kawa Security Model.

A collector should receive only the capabilities/resources required for its observation task.

Examples:

```text
resource.read_inventory
resource.scan_vulnerability
observation.emit
```

Scanner credentials, privileged OS credentials, API keys, or secrets SHOULD be mediated and MUST NOT be persisted in Observation Events.

Raw scanner output may itself contain sensitive information and must follow visibility and publication rules.

See `docs/security-model-v0.1.md` and `docs/publication-boundary.md`.

## 15. Deterministic collection versus LLM interpretation

Use a deterministic collector when the question is:

```text
What is the hostname?
Which OS version is installed?
How much RAM exists?
Which package version is installed?
Which CVEs did the scanner report?
Is this service running?
```

Use LLM reasoning when the question is closer to:

```text
Why does this combination of observations matter?
What Problem do these observations indicate?
What is the likely root cause?
What Plan should address it?
What should be reviewed next?
```

This gives Kawa a clean Observe/Orient boundary:

```text
Observe = deterministic tools whenever possible
Orient  = deterministic reducers + policy + LLM reasoning where useful
```

## 16. Tool replaceability

Kawa should be able to replace one collector with another without changing the durable semantic model.

For example:

```text
Ansible today
native collector tomorrow
cloud inventory API later
```

all may emit:

```text
system.hostname
hardware.ram.total_bytes
software.os.version
```

The tool is replaceable. The predicate meaning is durable.

## 17. LLM-friendly consequence

The LLM should not need to know how a value was scraped from an operating system unless provenance matters to the task.

Normal context can present:

```text
system.hostname = node-example
software.os.version = 24.04
hardware.ram.total_bytes = <bytes>
```

with refs to underlying Observation Events.

Detailed collector/source information remains available on demand.

This reduces context overhead while retaining provenance.

## 18. Acceptance tests

A conforming implementation should satisfy tests equivalent to:

```text
hostname can be collected and emitted without LLM inference.
Ansible can be replaced without changing canonical predicate meaning.
A vulnerability scanner finding remains evidence, not automatic organizational truth.
Collector failure does not become a false negative observation.
Raw scanner/tool output is not required in the Domain payload.
Every Observation is attributable to an authenticated observer workload.
The same normalized input produces the same typed Observation mapping.
```

## 19. Core rule

> **Use deterministic tools to observe the world. Use Kawa to preserve provenance and meaning. Use LLMs to reason where reasoning is actually required.**
