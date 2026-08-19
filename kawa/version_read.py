"""kawa version <subject> — one discovery surface for per-subject typed version facets (#140 V1).

> Do not unify versions. Unify version discovery.

A version is a POSITION reported from the coordinates the system already records, never an
authored number: build ← Git, event_frontier ← per-origin log positions, logical_time ← hlc,
policy_context ← policy-document digests, document_currency ← Status/supersession metadata,
schema_revision ← migration ledger, package ← packaging metadata (annotated non-system).
A derived read, never persisted; approved plan `plan-version-read` (#140 rev 4), bound by the
#142 invariants: local authority, shared vocabulary, explicit basis, loud unknowns.

Facet contract (rev 4):
- each SubjectKind has a CLOSED facet schema (SUBJECT_FACETS); an out-of-schema facet is
  absent (not applicable — a schema fact), an in-schema facet ALWAYS appears: either
  value + basis, or status=unknown + basis_kind=unknown + a closed reason code.
- unknowns stay loud in the compact line: `facet:UNKNOWN(reason)` — never omitted.
- exit taxonomy (scripts never parse prose):
    0 success · 2 malformed subject · 3 unknown subject id · 4 authority read failure
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import psycopg

from kawa.domain.ids import hlc_order_sql

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC_ROOT = os.path.join(REPO_ROOT, "docs")

SCHEMA_VERSION = 1
SUBJECT_KINDS = ("node", "doc", "policy", "schema", "package")
# frozen V1 policy-name registry (rev 4 §1): exact-match, lowercase, no aliases; a
# well-formed unregistered name is unknown-subject-id (exit 3), never a guess.
POLICY_REGISTRY = {"durability": "docs/durability-policy-v0.1.md"}
# the closed per-subject facet schema (rev 4 §3) — the exactness test pins this registry;
# adding a facet appends (never reorders) and bumps SCHEMA_VERSION.
SUBJECT_FACETS: dict[str, tuple[str, ...]] = {
    "node": ("build", "event_frontier", "logical_time", "policy_context", "schema_revision"),
    "doc": ("build", "document_currency"),
    "policy": ("build", "policy_context"),
    "schema": ("schema_revision",),
    "package": ("build", "package"),
}
BASIS_KINDS = ("authoritative_source", "deterministic", "git_clean_blob", "worktree_dirty", "unknown")
REASON_CODES = ("git_unavailable", "db_unreachable", "read_failed", "parse_failed")

EXIT_OK, EXIT_MALFORMED, EXIT_UNKNOWN_SUBJECT, EXIT_AUTHORITY_FAILURE = 0, 2, 3, 4


class SubjectError(Exception):
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _facet(value, basis_kind: str):  # type: ignore[no-untyped-def]
    return {"status": "known", "value": value, "basis_kind": basis_kind}


def _unknown(reason: str):  # type: ignore[no-untyped-def]
    assert reason in REASON_CODES
    return {"status": "unknown", "basis_kind": "unknown", "reason": reason}


# ---- subject parsing (rev 4 §1/§2) ----

_SUBJECT_RE = re.compile(r"^(node|doc|policy|schema|package)(?::(.*))?$")


def parse_subject(arg: str | None) -> tuple[str, str | None]:
    """Return (kind, subject_id). No argument resolves EXPLICITLY to the local node —
    the default is printed, never implicit. Malformed => exit 2 naming the vocabulary."""
    if arg is None:
        return "node", None                              # local node; rendered explicitly
    m = _SUBJECT_RE.match(arg)
    if not m:
        raise SubjectError(EXIT_MALFORMED,
                           f"malformed subject {arg!r} — vocabulary: node[:<id>], "
                           "doc:<relative_path>, policy:<name>, schema, package")
    kind, sid = m.group(1), m.group(2)
    if kind in ("schema", "package") and sid is not None:
        raise SubjectError(EXIT_MALFORMED, f"subject '{kind}' takes no id")
    # an EMPTY id is a syntax violation for every kind (review 110ea8d9 F1: 'node:' must be
    # exit 2 like 'doc:'/'policy:', never an exit-3 query for a node named "")
    if sid == "":
        raise SubjectError(EXIT_MALFORMED,
                           f"empty subject id in {arg!r} — vocabulary: node[:<id>], "
                           "doc:<relative_path>, policy:<name>, schema, package")
    if kind in ("doc", "policy") and sid is None:
        raise SubjectError(EXIT_MALFORMED, f"subject '{kind}' requires an id ({kind}:<...>)")
    return kind, sid


def _resolve_doc(sid: str) -> str:
    """Containment + canonicalization (rev 4 §2): POSIX separators, docs/ root only;
    absolute paths, traversal, and symlink escapes are MALFORMED (exit 2); a contained
    path with no file is unknown subject id (exit 3)."""
    if "\\" in sid or os.path.isabs(sid):
        raise SubjectError(EXIT_MALFORMED, f"doc path must be a POSIX-relative path under docs/: {sid!r}")
    candidate = os.path.realpath(os.path.join(DOC_ROOT, sid))
    if not (candidate + os.sep).startswith(os.path.realpath(DOC_ROOT) + os.sep) \
            and candidate != os.path.realpath(DOC_ROOT):
        raise SubjectError(EXIT_MALFORMED, f"doc path escapes docs/ (traversal/symlink rejected): {sid!r}")
    if not os.path.isfile(candidate):
        raise SubjectError(EXIT_UNKNOWN_SUBJECT, f"no such document under docs/: {sid!r}")
    return candidate


def _local_node_ref() -> str | None:
    try:
        with open(os.path.expanduser("~/.kawa/node_credential.json"), encoding="utf-8") as f:
            return json.load(f)["node_ref"]
    except (OSError, ValueError, KeyError):
        return None


# ---- facet readers (each names its authority; failures are typed unknowns) ----

def _git(args: list[str]) -> str:
    out = subprocess.run(["git", "-C", REPO_ROOT, *args], capture_output=True, text=True, timeout=10)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return out.stdout.strip()


def _read_build():  # type: ignore[no-untyped-def]
    try:
        commit = _git(["rev-parse", "--short", "HEAD"])
        dirty = bool(_git(["status", "--porcelain"]))
        return _facet({"commit": commit, "is_dirty": dirty}, "authoritative_source")
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return _unknown("git_unavailable")


def _tree_state(path: str) -> str:
    """git_clean_blob when the file matches HEAD; worktree_dirty otherwise (rev 4 §—
    dirty-tree honesty: uncommitted content is never presented as committed state)."""
    try:
        rel = os.path.relpath(path, REPO_ROOT)
        return "worktree_dirty" if _git(["status", "--porcelain", "--", rel]) else "git_clean_blob"
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return "worktree_dirty"                          # cannot prove clean => never claim clean


def _read_policy_context():  # type: ignore[no-untyped-def]
    import hashlib
    digests = {}
    tree = "git_clean_blob"
    for name, rel in POLICY_REGISTRY.items():
        path = os.path.join(REPO_ROOT, rel)
        try:
            with open(path, "rb") as f:
                digests[name] = "sha256:" + hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return _unknown("read_failed")
        if _tree_state(path) == "worktree_dirty":
            tree = "worktree_dirty"
    return _facet(digests, tree)


def _read_document_currency(path: str):  # type: ignore[no-untyped-def]
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(4096)
    except OSError:
        return _unknown("read_failed")
    m = re.search(r"^Status:\s*(.+)$", head, re.MULTILINE)
    if not m:
        return _unknown("parse_failed")                  # no Status header (doc rule violation)
    return _facet({"status": m.group(1).strip(),
                   "path": os.path.relpath(path, REPO_ROOT)}, _tree_state(path))


def _read_package():  # type: ignore[no-untyped-def]
    path = os.path.join(REPO_ROOT, "pyproject.toml")
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.MULTILINE)
    except OSError:
        return _unknown("read_failed")
    if not m:
        return _unknown("parse_failed")
    # packaging metadata is its OWN dimension — never the system version (#140 rev 2)
    return _facet({"version": m.group(1), "note": "packaging metadata — NOT the system version"},
                  _tree_state(path))


def _db_facets(want: set[str]) -> dict:  # type: ignore[type-arg]
    """event_frontier / logical_time / schema_revision from the log DB. One connection;
    a refused/unreachable DB yields typed unknowns for every requested DB facet."""
    out: dict = {}
    try:
        from kawa.storage.db import connect
        conn = connect()
    # narrow catch (review 69866836 F4): connectivity/config failures only — connect()'s
    # fail-closed refusal raises RuntimeError by design; a programming error must CRASH,
    # never masquerade as db_unreachable
    except (psycopg.Error, OSError, RuntimeError):
        return {f: _unknown("db_unreachable") for f in want}
    try:
        with conn.cursor() as cur:
            if "event_frontier" in want:
                cur.execute("SELECT origin_node, max(origin_seq) FROM events GROUP BY origin_node")
                out["event_frontier"] = _facet({r[0]: r[1] for r in cur.fetchall()},
                                               "authoritative_source")
            if "logical_time" in want:
                cur.execute(f"SELECT hlc FROM events ORDER BY {hlc_order_sql(unique='event_id')} LIMIT 1")
                row = cur.fetchone()
                out["logical_time"] = _facet(row[0] if row else None, "authoritative_source")
            if "schema_revision" in want:
                cur.execute("SELECT filename FROM schema_migrations ORDER BY filename DESC LIMIT 1")
                row = cur.fetchone()
                cur.execute("SELECT count(*) FROM schema_migrations")
                out["schema_revision"] = _facet({"head": row[0] if row else None,
                                                 "applied": cur.fetchone()[0]},
                                                "authoritative_source")
    except psycopg.Error:
        for f in want - set(out):
            out[f] = _unknown("db_unreachable")
    finally:
        conn.close()
    return out


# ---- the read ----

def version_read(arg: str | None = None) -> dict:
    """The V1 read: per-subject typed facets, closed schema, explicit unknowns.
    Raises SubjectError for exit-2/exit-3 conditions; never guesses."""
    kind, sid = parse_subject(arg)
    subject_display = kind
    doc_path = None
    if kind == "node":
        local = _local_node_ref()
        if sid is not None and sid != local:
            raise SubjectError(EXIT_UNKNOWN_SUBJECT,
                               f"node {sid!r} is not the local node ({local!r}) — V1 answers "
                               "for the local node only (remote subjects arrive with a "
                               "replication-aware read, deferred)")
        subject_display = f"node:{local or 'UNKNOWN(read_failed)'}"
    elif kind == "doc":
        doc_path = _resolve_doc(sid)                     # exit 2/3 on violation
        subject_display = f"doc:{os.path.relpath(doc_path, DOC_ROOT)}"
    elif kind == "policy":
        if sid not in POLICY_REGISTRY:
            raise SubjectError(EXIT_UNKNOWN_SUBJECT,
                               f"unknown policy {sid!r} — registered: {sorted(POLICY_REGISTRY)}")
        subject_display = f"policy:{sid}"

    schema = SUBJECT_FACETS[kind]
    facets: dict = {}
    db_wanted = {f for f in schema if f in ("event_frontier", "logical_time", "schema_revision")}
    if db_wanted:
        facets.update(_db_facets(db_wanted))
    for f in schema:
        if f in facets:
            continue
        if f == "build":
            facets[f] = _read_build()
        elif f == "policy_context":
            facets[f] = _read_policy_context()
        elif f == "document_currency":
            facets[f] = _read_document_currency(doc_path)  # type: ignore[arg-type]
        elif f == "package":
            facets[f] = _read_package()
    return {"schema_version": SCHEMA_VERSION, "subject": subject_display,
            "subject_kind": kind, "facets": facets}


def _compact_value(name: str, facet: dict) -> str:
    if facet["status"] == "unknown":
        return f"{name}:UNKNOWN({facet['reason']})"      # loud without --json (rev 4 §4)
    v = facet["value"]
    if name == "build":
        return f"git:{v['commit']}{'(dirty)' if v['is_dirty'] else ''}"
    if name == "event_frontier":
        inner = ",".join(f"{k}:{n}" for k, n in sorted(v.items()))
        return f"frontier:{{{inner}}}"
    if name == "logical_time":
        return f"hlc:{v or '—'}"                         # empty log: '—', never 'None' (F5)
    if name == "policy_context":
        inner = ",".join(f"{k}:{d[7:15]}" for k, d in sorted(v.items()))
        dirty = "(dirty)" if facet["basis_kind"] == "worktree_dirty" else ""
        return f"policy:{{{inner}}}{dirty}"
    if name == "schema_revision":
        return f"schema:{(v['head'] or '—').split('_')[0]}"
    if name == "document_currency":
        dirty = "(dirty)" if facet["basis_kind"] == "worktree_dirty" else ""
        # review 110ea8d9 F3: the Status header is repository CONTENT flowing into the
        # one-line surface — sanitize (printable only) and bound it so a crafted header
        # cannot inject delimiters/escapes into what agents parse
        status = "".join(ch for ch in v["status"] if ch.isprintable())[:60]
        return f"status:{status!r}{dirty}"
    if name == "package":
        return f"package:{v['version']}(non-system)"
    return f"{name}:?"


def render_compact(read: dict) -> str:
    """One line, every in-schema facet present — unknowns marked, never omitted."""
    parts = [_compact_value(n, read["facets"][n]) for n in SUBJECT_FACETS[read["subject_kind"]]]
    return f"{read['subject']} " + " ".join(parts)


def exit_code_for(read: dict) -> int:
    return EXIT_AUTHORITY_FAILURE if any(
        f["status"] == "unknown" for f in read["facets"].values()) else EXIT_OK
