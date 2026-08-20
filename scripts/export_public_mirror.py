"""Deterministic public-mirror export (plan-publication-mirror w-pub2).

Builds the PUBLIC repository as a derived, rebuildable projection of the
private development repo — owner decision on #152, conditions 1-3 and 6:

  * the private repository is never rewritten (this reads via fast-export);
  * every exported commit's author/committer identity is rewritten to the
    operator's PUBLIC identity (no personal address ever enters the export);
  * every exported commit message gains a `Source-Commit: <private-sha>`
    trailer — the private SHA stays the authoritative provenance coordinate;
  * the gate-1 publication linter must pass over the export's FULL history
    (every blob, not just HEAD) or the export is deleted and the run fails.

Determinism: fast-export preserves author/committer dates, and the rewrite
is a pure function of the input stream — two runs over the same source ref
produce byte-identical histories (verified by the self-test in CI).

Usage:
  KAWA_EXPORT_NAME=<public-name> KAWA_EXPORT_EMAIL=<id+user@users.noreply.github.com> \
      python scripts/export_public_mirror.py [--source .] [--dest ../kawa-public-export] \
                                             [--ref main]

Both identity variables are REQUIRED (fail-closed — there is no default
operator identity), and the email must be a noreply/public-safe address per
the same allowlist the linter enforces."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

_ALLOWED_EMAIL = re.compile(r"@users\.noreply\.github\.com$|@example\.(com|org|invalid)$")
_IDENT = re.compile(rb"^(author|committer|tagger) (.*) <[^>]*> (\d+ [+-]\d{4})$")


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, **kw)


def rewrite_stream(stream: bytes, name: str, email: str) -> bytes:
    """Pure byte-stream rewrite: identities in RECORD HEADERS only, trailer
    into COMMIT MESSAGE data blocks only. Every `data N` payload (including
    blob contents) is copied verbatim and never pattern-matched — a file that
    happens to contain author-like lines or `data N` text must survive
    byte-identical."""
    ident = f"{name} <{email}>".encode()
    out = bytearray()
    i, n = 0, len(stream)
    record: bytes | None = None      # b"commit" | b"tag" | b"blob" | None
    original: bytes | None = None
    while i < n:
        j = stream.find(b"\n", i)
        if j == -1:
            j = n
        line = stream[i:j]
        nxt = j + 1
        if line.startswith(b"commit ") or line == b"commit":
            record, original = b"commit", None
        elif line.startswith(b"tag ") :
            record, original = b"tag", None
        elif line.startswith(b"blob"):
            record, original = b"blob", None
        elif line.startswith(b"reset "):
            record, original = None, None
        if line.startswith(b"original-oid "):
            original = line.split(b" ", 1)[1]
            out += line + b"\n"
            i = nxt
            continue
        m = _IDENT.match(line)
        if m and record in (b"commit", b"tag"):
            out += m.group(1) + b" " + ident + b" " + m.group(3) + b"\n"
            i = nxt
            continue
        if line.startswith(b"data "):
            size = int(line[5:])
            payload = stream[nxt:nxt + size]
            if record == b"commit" and original is not None:
                msg = payload if payload.endswith(b"\n") else payload + b"\n"
                msg += b"\nSource-Commit: " + original + b"\n"
                out += b"data " + str(len(msg)).encode() + b"\n" + msg
                original = None          # one message per commit record
            else:
                out += line + b"\n" + payload
            i = nxt + size
            continue
        out += line + b"\n" if j < n else line
        i = nxt
    return bytes(out)


def lint_full_history(dest: str) -> list[str]:
    """Run the gate-1 scanner over EVERY blob in the export (identifier scan
    of the whole publishable history, not just the final tree).

    Reviewed exceptions come from the export's OWN baseline (#201). Before
    this, the export gate ignored the baseline the repo gate honours, so the
    two halves of one mechanism disagreed about what had been reviewed: an
    accepted finding could never ship, which pushes the next person to weaken
    a RULE — permanently and for every file — instead of recording one
    audited exception. Reading the baseline out of `dest` keeps it honest:
    the exception must ship with the content it excuses."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from lint_publication_boundary import (scan_text, finding_key, history_key, load_baseline,
                                           BASELINE, _SKIP_DIRS)
    known = load_baseline(dest)
    objs = _run(["git", "-C", dest, "rev-list", "--objects", "--all"]).stdout
    findings: list[str] = []
    seen: set[str] = set()
    for line in objs.decode().splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2 or not parts[1]:
            continue
        oid, path = parts
        if path == BASELINE or any(path.startswith(d) for d in _SKIP_DIRS):
            continue        # the register of findings quotes them; scanning it is
                            # self-referential. _SKIP_DIRS is shared with the repo
                            # gate rather than restated, so the two halves cannot
                            # drift apart when something is added to it later.
        if oid in seen:
            continue
        seen.add(oid)
        typ = _run(["git", "-C", dest, "cat-file", "-t", oid]).stdout.strip()
        if typ != b"blob":
            continue
        raw = _run(["git", "-C", dest, "cat-file", "blob", oid]).stdout
        if b"\0" in raw[:4096]:
            continue
        for f in scan_text(raw.decode("utf-8", errors="replace"), path):
            # TWO accepted forms, because "reviewed" means two different things.
            #
            # A path key exempts the LIVE exception and, by construction, every
            # historical version of it — that is #201's design and it stays: a
            # reviewed fixture should not need re-approving for each of its own
            # past revisions.
            #
            # A blob key exempts ONE immutable object. It exists because the
            # path key is far too broad for a finding that lives ONLY in
            # history: baselining `README.md::private-repo::<owner>/<repo>` to
            # accept a 2026-06 README would also silence the same link if
            # someone put it back into the README tomorrow — a blind spot on
            # the most-edited file in the tree. Measured 2026-08-20: the
            # private-repo rule found exactly that, twelve already-published
            # links in two superseded README blobs.
            if finding_key(f) in known or history_key(f, oid) in known:
                continue
            findings.append(f"{f['rule']}: {path} ({oid[:10]}) {f['match']}"
                            f"\n      baseline key (this blob only): {history_key(f, oid)}")
    # identity sweep: no non-public email may survive anywhere in history
    log = _run(["git", "-C", dest, "log", "--all", "--format=%an <%ae>%n%cn <%ce>"]).stdout
    for ident in sorted(set(log.decode().splitlines())):
        m = re.search(r"<([^>]*)>", ident)
        if m and not _ALLOWED_EMAIL.search(m.group(1)):
            findings.append(f"identity: non-public email in history: {ident}")
    return findings


def export(source: str, dest: str, ref: str, name: str, email: str) -> str:
    if os.path.exists(dest):
        raise SystemExit(f"dest exists: {dest} (exports are rebuilt from scratch — remove it first)")
    stream = _run(["git", "-C", source, "fast-export", "--show-original-ids",
                   "--signed-tags=strip", "--tag-of-filtered-object=drop", ref]).stdout
    rewritten = rewrite_stream(stream, name, email)
    os.makedirs(dest)
    _run(["git", "-C", dest, "init", "-q", "-b", ref])
    subprocess.run(["git", "-C", dest, "fast-import", "--quiet"],
                   input=rewritten, check=True, capture_output=True)
    _run(["git", "-C", dest, "checkout", "-q", ref])
    findings = lint_full_history(dest)
    if findings:
        shutil.rmtree(dest)
        raise SystemExit("[export] GATE FAILED — export deleted:\n  " + "\n  ".join(findings[:40]))
    head = _run(["git", "-C", dest, "rev-parse", "HEAD"]).stdout.decode().strip()
    return head


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=".")
    ap.add_argument("--dest", default=os.path.expanduser("~/kawa-public-export"))
    ap.add_argument("--ref", default="main")
    args = ap.parse_args()
    name = os.environ.get("KAWA_EXPORT_NAME", "")
    email = os.environ.get("KAWA_EXPORT_EMAIL", "")
    if not name or not email:
        print("KAWA_EXPORT_NAME / KAWA_EXPORT_EMAIL are required (fail-closed: "
              "no default operator identity)", file=sys.stderr)
        return 2
    if not _ALLOWED_EMAIL.search(email):
        print(f"KAWA_EXPORT_EMAIL must be a noreply/public-safe address, got {email!r}",
              file=sys.stderr)
        return 2
    head = export(args.source, args.dest, args.ref, name, email)
    n = _run(["git", "-C", args.dest, "rev-list", "--count", "HEAD"]).stdout.decode().strip()
    print(f"[export] OK — {n} commits, HEAD {head}, gate-1 clean over full history")
    print(f"[export] dest: {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
