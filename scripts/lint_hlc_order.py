"""HLC ordering lint (#214 step 1) — one causal order, one spelling.

Kawa orders events by `(physical, logical, tiebreak)`. That was written out by
hand in six places, and #214's review found the resulting drift five separate
times — most sharply as `ORDER BY hlc`, which sorts TEXT and therefore puts
"1.10" below "1.2" while the Python key, parsing ints, puts it above. A window
selected by one order and ranked by another does not merely return less; it
drops rows the ranking would have chosen, silently and as a worse answer rather
than a failure.

The rule was written down in the plan and did not survive one revision. This is
the mechanism instead:

  * `ORDER BY hlc` (the text sort) is never correct — the numeric fields must be
    parsed, which is what makes 1.10 > 1.2.
  * `split_part(<something>hlc…)` spelled by hand anywhere outside the shared
    definition is a second representation of the one order.

Both fail the build. The sanctioned forms are `kawa.domain.ids.hlc_order_sql`
and `hlc_sort_key`, and `tests/test_hlc_order_parity.py` proves the two engines
agree — a shared definition is necessary and not sufficient, since collation and
integer boundaries live in the engines rather than in the string.

Usage:  python scripts/lint_hlc_order.py [--root DIR]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

PRAGMA = "hlc-order:allow"

# where the one definition lives, and the files whose job is to talk about it
_HOME = "kawa/domain/ids.py"
_ABOUT = ("scripts/lint_hlc_order.py", "tests/test_hlc_order_parity.py")

# Whole-TEXT, not per-line: review round 2 walked straight past both patterns with
# `ORDER BY\n    hlc DESC` and a split_part broken across string literals. And the
# original text-sort rule carried a `(?!\s*[,)])` lookahead meant to avoid matching
# `ORDER BY hlc_phys, ...` — it instead exempted `ORDER BY hlc, origin_node`, which is
# the single most likely hand-rolled form. The lookahead is gone; `\b` after `hlc`
# already stops `hlc_phys` from matching.
_TEXT_SORT = re.compile(r"\border\s+by\s+\(?\s*[\w.]*\bhlc\b", re.I | re.S)
_HANDROLLED = re.compile(r"split_part\s*\(\s*(?:[^()]*?)\bhlc\b", re.I | re.S)
# The Python half. Round 3 accepted the SQL rules and noted that a raw text sort on
# the Python side -- `sorted(rows, key=lambda r: r["hlc"])` -- was still invisible,
# which is the SAME defect from the other engine: text order puts 1.10 below 1.2.
# Narrow on purpose: a sort key that mentions hlc must come from hlc_sort_key.
_PY_TEXT_SORT = re.compile(r"\bkey\s*=(?:(?!hlc_sort_key)[^\n])*?\bhlc\b", re.I)
_SCAN_SUFFIXES = (".py", ".sql", ".md")

# Migrations that predate this lint. Applied files are historical — rewriting one to
# satisfy a lint changes what a replayed schema is — but skipping `sql/` wholesale
# would exempt every FUTURE migration too, and migrations define views, functions and
# expression indexes evaluated at query time (review round 1).
#
# A literal frontier rather than a directory listing: computing it from disk at import
# time raised FileNotFoundError outside a checkout and ignored `--root` entirely
# (review round 2). Everything at or after this name is scanned.
_MIGRATION_FRONTIER = "sql/0025_"


def _tracked(root: str) -> list[str]:
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                         capture_output=True, check=True)
    return [f for f in out.stdout.decode().split("\0") if f]


def scan_text(text: str, path: str = "<text>") -> list[tuple[int, str, str]]:
    """Scan the whole text so a pattern split across lines cannot hide. The pragma
    is honoured on the line the match STARTS on, and on the line before it, so a
    deliberate example can be annotated the way the other linters here allow."""
    lines = text.splitlines()
    findings: list[tuple[int, str, str]] = []
    rules = [("text-sort", _TEXT_SORT), ("hand-rolled", _HANDROLLED)]
    if path.endswith(".py"):
        rules.append(("py-text-sort", _PY_TEXT_SORT))
    for rule, pattern in rules:
        for m in pattern.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            context = lines[max(0, line_no - 2):line_no]
            if any(PRAGMA in c for c in context):
                continue
            snippet = " ".join(text[m.start():m.start() + 90].split())
            findings.append((line_no, rule, snippet))
    return sorted(findings)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    findings: list[tuple[str, int, str, str]] = []
    for rel in _tracked(root):
        if not rel.endswith(_SCAN_SUFFIXES) or rel == _HOME or rel in _ABOUT:
            continue
        # Applied migrations are historical -- rewriting one to satisfy a lint
        # would change what a replayed schema is. But skipping `sql/` wholesale
        # would also exempt every FUTURE migration, and migrations define views,
        # functions and expression indexes that are evaluated at run time
        # (review round 1). So the existing files are baselined by name and
        # anything new is scanned.
        if rel.startswith("sql/") and rel < _MIGRATION_FRONTIER:
            continue
        try:
            text = open(os.path.join(root, rel), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for line_no, rule, snippet in scan_text(text, rel):
            findings.append((rel, line_no, rule, snippet))

    for rel, line_no, rule, snippet in findings:
        print(f"[hlc-order] {rule}: {rel}:{line_no}  {snippet}")
    print(f"[hlc-order] {len(findings)} finding(s)")
    if findings:
        print("[hlc-order] FAIL — use kawa.domain.ids.hlc_order_sql / hlc_sort_key, "
              "or add an `hlc-order:allow` pragma for a deliberate example.")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
