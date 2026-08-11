#!/usr/bin/env python3
"""Kawa vocabulary / document-drift linter (issue #34, Theme E).

Structurally eliminates silent vocabulary divergence and document-set drift across
the docs/ contracts, using registry/vocabulary.json as the single source of truth.

Deliberately BOUNDED — it does not require "every word be registered" (that would
false-positive on English prose). It enforces four precise, high-value invariants:

  C1  event-token drift   — every `family.member` dotted token in docs must be a
                            registered event_type (or an allow-listed non-event token).
                            Catches typos and family drift (plan.created vs plan.proposed).
  C2  public-concept gaps — docs that declare the public vocabulary must list every
                            public concept (catches the review's "Claim missing from
                            the principle docs" class).
  C3  mechanism quarantine — no standards/provider name (SPIFFE, TPM, Postgres, ...)
                            may appear in a core_semantic_doc. Mechanizes the #24/#26/#30
                            promise that mechanics never leak into Core vocabulary.
  C4  document-set drift  — the set of `*-vN.M.md` docs referenced by the supersession
                            matrix, spec §26, and the README reading path must agree
                            (the review's matrix/§26/README triple-drift).

Known pre-existing drift is recorded in registry/drift-baseline.json so this can be
introduced to a repo that already has debt: baselined findings are reported but do not
fail CI; any NEW finding fails. `--update-baseline` re-freezes the current findings.
Exit 0 = no new drift; 1 = new drift; 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "registry" / "vocabulary.json"
BASELINE = REPO / "registry" / "drift-baseline.json"
DOCS = REPO / "docs"

DOTTED = re.compile(r"\b[a-z][a-z0-9]*(?:\.[a-z0-9_*]+)+\b")
DOCREF = re.compile(r"\b([a-z0-9][a-z0-9-]*-v\d+\.\d+\.md)\b")


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def finding(check: str, doc: str, detail: str) -> dict:
    return {"check": check, "doc": doc, "detail": detail}


def c1_event_tokens(reg: dict, docs: list[Path]) -> list[dict]:
    allowed = set(reg["event_types"]) | set(reg["non_event_dotted_allow"])
    catalog = set(reg.get("event_catalog_docs", []))
    out = []
    for d in docs:
        if d.name not in catalog:
            continue
        text = d.read_text(encoding="utf-8")
        for tok in sorted(set(DOTTED.findall(text))):
            # Only judge tokens that look like a Domain event family.member.
            family = tok.split(".", 1)[0]
            if family in {"project", "problem", "plan", "observation",
                          "claim", "review", "finding", "approval", "result"}:
                if tok not in allowed:
                    out.append(finding("C1-event-token", d.name, tok))
    return out


def c2_public_concepts(reg: dict, docs_by_name: dict[str, Path]) -> list[dict]:
    concepts = reg["public_concepts"]
    out = []
    for name in reg["public_vocab_docs"]:
        p = docs_by_name.get(name)
        if not p:
            out.append(finding("C2-public-concept", name, "declared public-vocab doc is missing"))
            continue
        text = p.read_text(encoding="utf-8")
        for c in concepts:
            if not re.search(rf"\b{re.escape(c)}\b", text):
                out.append(finding("C2-public-concept", name, f"missing public concept: {c}"))
    return out


def c3_quarantine(reg: dict, docs_by_name: dict[str, Path]) -> list[dict]:
    terms = reg["mechanism_terms"]
    out = []
    for name in reg["core_semantic_docs"]:
        p = docs_by_name.get(name)
        if not p:
            continue
        text = p.read_text(encoding="utf-8")
        for t in terms:
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", text):
                out.append(finding("C3-quarantine", name, f"mechanism term in Core doc: {t}"))
    return out


def _ver(name: str) -> tuple[str, tuple[int, int]]:
    m = re.match(r"(.+)-v(\d+)\.(\d+)\.md$", name)
    return (m.group(1), (int(m.group(2)), int(m.group(3)))) if m else (name, (0, 0))


def c4_docset(docs_by_name: dict[str, Path]) -> list[dict]:
    """Compare the CURRENT-document sets named by the three index sources. A doc is
    only a subject of drift if it is current (no higher-version sibling exists) and is
    not one of the index docs themselves (which never self-list)."""
    def refs(name: str) -> set[str]:
        p = docs_by_name.get(name)
        return set(DOCREF.findall(p.read_text(encoding="utf-8"))) if p else set()
    readme_p = REPO / "README.md"
    sources = {
        "matrix": refs("supersession-matrix-v0.1.md"),
        "spec-26": refs("specification-v0.4.md"),
        "readme": set(DOCREF.findall(readme_p.read_text(encoding="utf-8"))) if readme_p.exists() else set(),
    }
    existing = set(docs_by_name)
    # highest version present per stem → anything below it is superseded, not drift.
    highest: dict[str, tuple[int, int]] = {}
    for n in existing:
        stem, v = _ver(n)
        if v > highest.get(stem, (-1, -1)):
            highest[stem] = v
    indices = {"supersession-matrix-v0.1.md", "specification-v0.4.md"}

    def is_current(n: str) -> bool:
        stem, v = _ver(n)
        return n in existing and highest.get(stem) == v

    out = []
    names = sorted(indices := (sources["matrix"] | sources["spec-26"] | sources["readme"]))
    for x in names:
        if not is_current(x) or x in {"supersession-matrix-v0.1.md", "specification-v0.4.md"}:
            continue
        present = [s for s, refset in sources.items() if x in refset]
        absent = [s for s, refset in sources.items() if x not in refset]
        if present and absent:
            out.append(finding("C4-docset", x,
                               f"current doc listed in {'+'.join(present)} but missing from {'+'.join(absent)}"))
    return out


def key(f: dict) -> str:
    return f"{f['check']}|{f['doc']}|{f['detail']}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update-baseline", action="store_true",
                    help="re-freeze current findings as the accepted baseline")
    args = ap.parse_args()

    if not REGISTRY.exists():
        print(f"[drift] registry not found: {REGISTRY}", file=sys.stderr)
        return 2
    reg = load_json(REGISTRY)
    docs = sorted(DOCS.glob("*.md"))
    docs_by_name = {p.name: p for p in docs}

    findings = (c1_event_tokens(reg, docs)
                + c2_public_concepts(reg, docs_by_name)
                + c3_quarantine(reg, docs_by_name)
                + c4_docset(docs_by_name))

    if args.update_baseline:
        BASELINE.write_text(json.dumps(
            {"baselined": sorted(key(f) for f in findings)}, indent=2) + "\n",
            encoding="utf-8")
        print(f"[drift] baseline re-frozen: {len(findings)} finding(s) accepted as known drift")
        return 0

    baseline = set(load_json(BASELINE).get("baselined", [])) if BASELINE.exists() else set()
    new = [f for f in findings if key(f) not in baseline]
    known = [f for f in findings if key(f) in baseline]

    for f in sorted(new, key=key):
        print(f"NEW  {f['check']:18} {f['doc']:42} {f['detail']}")
    for f in sorted(known, key=key):
        print(f"known {f['check']:17} {f['doc']:42} {f['detail']}")

    print(f"\n[drift] {len(new)} new, {len(known)} known(baselined), "
          f"{len(docs)} docs scanned against registry v{reg.get('version')}")
    if new:
        print("[drift] FAIL — new vocabulary/document drift. Register the term, fix the "
              "doc, or (if intentional and reviewed) run --update-baseline.")
        return 1
    print("[drift] OK — no new drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
