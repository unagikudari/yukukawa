"""#156 Phase A — deterministic normative-section index over the repository.

Derived and disposable, never authoritative: the documents remain the SoT; this
module builds a per-commit index of their sections so `repository_normative`
retrieval candidates can be answered with provenance `{doc_path, heading_path,
authority_status, content_digest, commit}`.

Determinism contract (#156 sketch 4):
- Sections are read from the **git object store** at a stated commit (committed
  blob bytes — never the working tree), so the index is identical on every
  machine for the same commit, independent of local edits or line-ending config.
- The working tree is consulted ONLY to detect dirty docs; a dirty doc's
  sections are withheld by the caller with a `repository_dirty` frontier.
- `section_anchor = sha256(doc_path \\0 heading_path)` is content-independent
  (survives text edits; heading renames/moves intentionally mint a new anchor).
  `content_digest = sha256(committed section bytes)` pins the exact text.
- The index is also exportable/loadable as a JSON manifest (build artifact) so
  a node without a checkout can answer read-only with stated provenance; the
  checkout path and the manifest path yield byte-identical candidates for the
  same commit (asserted by test).

Current-authority filtering derives from the supersession matrix at the SAME
commit: only docs listed under its "Current …" sections are indexed.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_MATRIX = "docs/supersession-matrix-v0.1.md"
_EXCERPT_CAP = 320          # hard per-candidate excerpt cap (#156 sketch 6)


@dataclass(frozen=True)
class DocSection:
    doc_path: str
    heading: str                 # exact heading text (without leading #'s)
    heading_path: str            # " / "-joined ancestor headings + own
    section_anchor: str          # sha256(doc_path \0 heading_path) hex
    content_digest: str          # sha256(committed section bytes) hex
    excerpt: str                 # bounded, deterministic (<= _EXCERPT_CAP chars)

    @property
    def anchor8(self) -> str:
        return self.section_anchor[:8]


@dataclass(frozen=True)
class SectionIndex:
    commit: str
    source: str                  # 'checkout' | 'manifest'
    sections: tuple[DocSection, ...]
    dirty_docs: tuple[str, ...]  # doc paths whose working tree differs from the commit blob

    def by_anchor_prefix(self, prefix: str) -> list[DocSection]:
        return [s for s in self.sections if s.section_anchor.startswith(prefix)]

    def resolve(self, anchor_ref: str) -> DocSection | None:
        """Resolve a registry-style anchor 'doc_path#heading text' to a section.
        Returns None when the heading no longer resolves (stale mapping) or is
        ambiguous — ambiguity is a lint failure upstream, never a silent pick."""
        if "#" not in anchor_ref:
            return None
        doc_path, heading = anchor_ref.split("#", 1)
        hits = [s for s in self.sections if s.doc_path == doc_path and s.heading == heading]
        return hits[0] if len(hits) == 1 else None

    def to_manifest(self) -> str:
        return json.dumps({
            "commit": self.commit,
            "sections": [{
                "doc_path": s.doc_path, "heading": s.heading, "heading_path": s.heading_path,
                "section_anchor": s.section_anchor, "content_digest": s.content_digest,
                "excerpt": s.excerpt,
            } for s in self.sections],
        }, sort_keys=True, ensure_ascii=False, indent=1)


def load_manifest(text: str) -> SectionIndex:
    d = json.loads(text)
    return SectionIndex(
        commit=d["commit"], source="manifest",
        sections=tuple(DocSection(**s) for s in d["sections"]),
        dirty_docs=(),           # a manifest has no working tree; dirtiness is a checkout concept
    )


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(("git", "-C", str(repo)) + args, check=True,
                          capture_output=True).stdout


def _blob(repo: Path, commit: str, path: str) -> bytes | None:
    try:
        return _git(repo, "show", f"{commit}:{path}")
    except subprocess.CalledProcessError:
        return None


_FENCE = re.compile(r"^```")
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def current_authority_docs(matrix_text: str) -> list[str]:
    """Docs listed in fenced blocks under the matrix's 'Current …' H2 sections,
    excluding 'supersedes …' continuation lines. Deterministic text parse of the
    matrix at the SAME commit as the index — the matrix stays the authority SoT."""
    docs: list[str] = []
    in_current = False
    in_fence = False
    for line in matrix_text.splitlines():
        m = _HEADING.match(line)
        if m and len(m.group(1)) == 2:
            in_current = m.group(2).startswith("Current")
            continue
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not (in_current and in_fence):
            continue
        token = line.strip().split()[0] if line.strip() else ""
        if token.endswith(".md") and not line.strip().startswith("supersedes"):
            docs.append(f"docs/{token}" if not token.startswith("docs/") else token)
    seen: set[str] = set()
    return [d for d in docs if not (d in seen or seen.add(d))]


def _excerpt_of(body: bytes) -> str:
    """Deterministic bounded excerpt: the section body's first sentences up to the
    cap, cut at a sentence boundary where one exists, with an ellipsis marker."""
    text = body.decode("utf-8", errors="replace")
    # drop the heading line itself and leading blank lines
    lines = text.splitlines()
    content = "\n".join(lines[1:]).strip()
    flat = re.sub(r"\s+", " ", content).strip()
    if len(flat) <= _EXCERPT_CAP:
        return flat
    cut = flat[:_EXCERPT_CAP]
    dot = max(cut.rfind(". "), cut.rfind("。"))
    if dot > _EXCERPT_CAP // 2:
        cut = cut[:dot + 1]
    return cut.rstrip() + " …"


def _split_sections(doc_path: str, blob: bytes) -> list[DocSection]:
    """ATX-heading parse over committed blob bytes, byte-offset spans. A section
    runs from its heading line to the next heading of the same-or-higher level.
    Fenced code blocks are opaque (a '# ' inside a fence is not a heading)."""
    lines = blob.split(b"\n")
    heads: list[tuple[int, int, str]] = []            # (line_no, level, heading_text)
    in_fence = False
    for i, raw in enumerate(lines):
        line = raw.decode("utf-8", errors="replace")
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2)))
    out: list[DocSection] = []
    stack: list[tuple[int, str]] = []                 # (level, heading) ancestry
    for idx, (line_no, level, heading) in enumerate(heads):
        while stack and stack[-1][0] >= level:
            stack.pop()
        heading_path = " / ".join(h for _, h in stack + [(level, heading)])
        stack.append((level, heading))
        end_line = len(lines)
        for nline, nlevel, _ in heads[idx + 1:]:
            if nlevel <= level:
                end_line = nline
                break
        span = b"\n".join(lines[line_no:end_line])
        anchor = hashlib.sha256(f"{doc_path}\0{heading_path}".encode()).hexdigest()
        out.append(DocSection(
            doc_path=doc_path, heading=heading, heading_path=heading_path,
            section_anchor=anchor,
            content_digest=hashlib.sha256(span).hexdigest(),
            excerpt=_excerpt_of(span),
        ))
    return out


# per-commit cache: commit -> (sections_by_doc, doc_blob_digest). Sections for a
# commit are immutable; only working-tree dirtiness varies per call and is
# recomputed fresh every time (it is the one non-commit-determined input).
_CACHE: dict[str, tuple[dict[str, tuple[DocSection, ...]], dict[str, str]]] = {}


def build_index(repo: Path, commit: str | None = None) -> SectionIndex:
    """Build the index from the git object store at `commit` (default HEAD).
    Deterministic in-memory build — there is deliberately no maintenance command
    and no on-disk cache to go stale (#156 sketch 4)."""
    resolved = _git(repo, "rev-parse", commit or "HEAD").decode().strip()
    if resolved not in _CACHE:
        matrix = _blob(repo, resolved, _MATRIX)
        by_doc: dict[str, tuple[DocSection, ...]] = {}
        digests: dict[str, str] = {}
        if matrix is not None:
            for doc in current_authority_docs(matrix.decode("utf-8", errors="replace")):
                blob = _blob(repo, resolved, doc)
                if blob is None:
                    continue                           # matrix lists it, tree lacks it: lint's problem
                by_doc[doc] = tuple(_split_sections(doc, blob))
                digests[doc] = hashlib.sha256(blob).hexdigest()
        _CACHE[resolved] = (by_doc, digests)
    by_doc, digests = _CACHE[resolved]
    sections: list[DocSection] = []
    dirty: list[str] = []
    for doc, secs in by_doc.items():
        wt = repo / doc
        if wt.exists() and hashlib.sha256(wt.read_bytes()).hexdigest() != digests[doc]:
            dirty.append(doc)                          # withheld: caller states repository_dirty
            continue
        sections.extend(secs)
    return SectionIndex(commit=resolved, source="checkout",
                        sections=tuple(sections), dirty_docs=tuple(dirty))
