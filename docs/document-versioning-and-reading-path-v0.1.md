# Kawa Document Versioning and Reading Path v0.1

Status: Normative repository documentation rule

## 1. Purpose

Architecture documents evolve quickly. A superseded document must never look equally authoritative to a new human or LLM reader.

## 2. Rules

Every versioned design document MUST state one of:

```text
Status: Current normative
Status: Draft, current
Status: Superseded by: <path>
Status: Historical
```

A superseded document MUST identify its successor near the top of the file.

A current document SHOULD identify the previous version it supersedes.

## 3. One obvious reading path

`README.md` is the canonical entry point.

It MUST link the current architecture reading path directly.

A capable new reader SHOULD NOT have to discover current documents by browsing the directory or comparing version numbers.

## 4. Version dimensions stay distinct

Do not conflate:

```text
logical schema version
physical PostgreSQL schema version
Event taxonomy version
Reducer contract version
MCP contract version
security model version
```

For example, `postgresql-physical-schema-v0.3.md` does not imply that `core-logical-schema-v0.3.md` exists unless that file actually exists.

## 5. Historical documents

Historical documents MAY remain in the repository for design traceability, but they MUST NOT appear in the primary reading path except as history.

Do not delete useful architecture history merely to make the directory look clean.

## 6. Link audit

Before architecture review or implementation gate:

```text
README current links resolve
specification current links resolve
no current document points to a nonexistent version
superseded files identify successor
current files identify status
```

This SHOULD become an automated repository check once implementation tooling exists.

## 7. Core rule

> **History may remain. Authority must be obvious.**
