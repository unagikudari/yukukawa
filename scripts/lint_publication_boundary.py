"""Gate-1 publication-boundary linter (#152, plan-publication-mirror w-pub1).

Mechanizes the PROHIBITED side of docs/publication-boundary.md over tracked
content: operator-identifying coordinates that must never be published. Bare
node/agent nicknames stay legal (the dogfood-evidence exception) — this tool
only greps what is regular enough to grep:

  url-host     URL/DSN syntax carrying a non-allowlisted host component
  ip           private-network IPv4 (RFC 1918), ULA/link-local IPv6, or any
               non-documentation public IPv4 (RFC 5737 ranges are allowed)
  dns          DNS-shaped tokens outside the public-safe allowlist
  home-path    /home/<user> or /Users/<user> operator paths (%h stays legal)
  email        address-bearing tokens outside the noreply/doc allowlist

Baseline mechanism mirrors lint_vocabulary_drift: known findings live in
registry/publication-baseline.json; NEW findings fail (exit 1);
--update-baseline rewrites the file after human review. Lines carrying the
pragma `pub-lint:allow` are skipped (for deliberate in-repo examples such as
linter test fixtures).

Usage:
  python scripts/lint_publication_boundary.py [--update-baseline] [--root DIR]

--root lets the same gate run against an EXPORT tree (w-pub2), which is the
publication surface this linter ultimately protects. Runtime databases are
deliberately out of scope (boundary §Design rule)."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

PRAGMA = "pub-lint:allow"
# POSIX-separated on purpose: this is compared against `git ls-files` /
# `git rev-list --objects` output, which is always "/"-separated regardless of
# platform. os.path.join would yield "\" on Windows and silently stop matching,
# so the export gate would scan its own register of findings.
BASELINE = "registry/publication-baseline.json"

_DOC_V4 = re.compile(r"^(192\.0\.2|198\.51\.100|203\.0\.113)\.")
_ALLOWED_V4 = re.compile(r"^(127\.|0\.0\.0\.0$|255\.255\.255\.255$)")
_IPV4 = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3})\b")
_IPV6_BAD = re.compile(r"\b(f[cd][0-9a-f]{2}:[0-9a-f:]{2,}|fe80:[0-9a-f:]{2,})", re.I)
# `?` and `#` terminate the host as surely as `/` does. Without them a URL with
# no path swallowed its query into the host, so the reported match was the
# meaningless `api.internal?query=x` instead of the coordinate `api.internal`.
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^/\s\"'<>@]*@)?([^/\s\"'<>:?#]+)", re.I)
_DSN_HOST = re.compile(r"\bhost=([^\s\"']+)")
# The FIRST label may be a single character, like every other label. Requiring
# two exempted `a.corp.io` (pub-lint:allow) — a perfectly ordinary FQDN — and, because masking
# rewrites a placeholder to one character, it exempted every coordinate whose
# leading label was a template: `{host}.corp.io` became `x.corp.io` (pub-lint:allow) and scanned
# clean (pub-lint:allow). Round 3 of the #207 review built that bypass; the sentinel was only
# how it surfaced, so the length rule is what gets fixed, not the sentinel.
_DNS = re.compile(r"\b([a-z0-9][a-z0-9-]{0,62}(?:\.[a-z0-9][a-z0-9-]{0,62})+"
                  r"\.(?:net|com|org|io|dev|app|ai|cloud|sh|jp))\b")
# /home/ is case-insensitive on the account segment for the same reason /Users/
# already was: a Linux account may legitimately start with a capital, and the
# lowercase-only class let `/home/Alice` through while `/Users/Alice` was caught.  (pub-lint:allow)
_HOME = re.compile(r"(/home/[A-Za-z0-9][A-Za-z0-9_-]*|/Users/[A-Za-z0-9][A-Za-z0-9_-]*)")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

_ALLOWED_HOST_SUFFIXES = (
    ".invalid", ".example", ".test", ".localhost",
    "example.com", "example.org", "example.net",
    "github.com", "github.io", "githubusercontent.com",
    "apache.org", "python.org", "postgresql.org",
    "goatcounter.com", "zgo.at",
    "yukukawa.dev", "yukukawa.com",          # the project's own public site
    "anthropic.com", "claude.com",
    "w3.org", "json-schema.org", "schema.org",   # standards namespaces
)
_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")
_ALLOWED_EMAIL_SUFFIXES = ("@users.noreply.github.com", "@example.com",
                           "@example.org", "@example.invalid",
                           "noreply@anthropic.com")

_SKIP_DIRS = (".git/",)
_BINARY_HINT = b"\0"


# A placeholder is TEMPLATE SYNTAX, not "any pair of braces" (#201). The brace
# form is anchored to what a template token can actually look like — a bare
# identifier or dotted path — because the earlier `\{[^{}]*\}` also matched the
# innermost objects of a JSON document. Under the fixpoint loop below those
# collapse outward, so a one-line JSON file masked to a single `x` and the gate
# reported it clean: measured 2026-08-18 on a real captured runtime response
# carrying an operator home path and a `user@host` terminal title, 0 findings.
# The hole was never JSON-specific — any brace-balanced structure on one line
# (a Python/JS dict literal in a .py/.md) was erased the same way, which is why
# the fix is to the RULE rather than a per-extension structural parse.
#
# Direction of safety: masking too LITTLE can only cost a false positive (a
# reviewer triages it); masking too MUCH silently removes payload from a
# fail-closed gate. When in doubt this rule does not mask.
# EVERY form is anchored, not just the brace one. `\$\{[^{}]*\}` and
# `\$\([^()]*\)` are the same greedy bug wearing different syntax: a shell
# default-value expansion carries a literal payload, so `${DIR:-/home/alice}`  (pub-lint:allow)
# masked the operator path away exactly like the JSON case, and
# `$(curl http://10.x.x.x/token)` swallowed the address (pub-lint:allow). Found by adversarial
# review of the first cut of this fix, which narrowed only the braces.
#
# What each form may contain is therefore bounded to what a SUBSTITUTION NAME
# can be. A default value or a command argument is data and stays scannable.
# A DOTTED brace token is indistinguishable from an FQDN — `{node.name}` and
# `{vault.internal.net}` have identical structure (pub-lint:allow) — so allowing dots let a
# brace-wrapped hostname mask itself away (round 2 finding). The brace form is
# therefore ONE segment with no DOTS (hyphens are fine — see below).
#
# Nothing is lost by that: masking only matters when a placeholder is EMBEDDED
# in a coordinate (`foo.{ENV}.corp.io`), and there the token is single-segment.  (pub-lint:allow)
# An unmasked `{node.name}` raises no finding on its own because `_DNS` requires
# a real TLD — `name` is not one. Refusing to mask is free here; masking is not.
#
# The `(?<!\$)` keeps each syntax owned by exactly one alternative, so a dotted
# `${...}` cannot be half-consumed by the brace rule and laundered into `$x`.
_PLACEHOLDER = re.compile(
    # Hyphens are back: `{node-1}` is an ordinary template name, and a hostname
    # needs a DOT to be one, so allowing `-` cannot wrap an FQDN. Leaving it out
    # meant `{app-id}` survived unmasked into host position (round 4).
    r"(?<!\$)\{[A-Za-z_][A-Za-z0-9_-]*\}"   # {host}, {node-1} — NOT {a.b.net}  (pub-lint:allow)
    r"|\$\{[A-Za-z_][A-Za-z0-9_]*\}"    # ${VAR} — NOT ${VAR:-/home/alice}  (pub-lint:allow)
    r"|\$\([A-Za-z_][A-Za-z0-9_ -]*\)"  # $(hostname -f) — NOT $(echo host.corp.io)  (pub-lint:allow)
)


def _mask_placeholders(line: str) -> str:
    """Review (b): placeholders are MASKED to 'x', never skipped wholesale —
    `foo.{ENV}.corp.io` must still scan as masked and be caught. (pub-lint:allow)

    The loop resolves NESTED substitution (`$(a $(b))`); the brace form cannot
    nest, so it converges immediately."""
    prev = None
    while prev != line:
        prev, line = line, _PLACEHOLDER.sub("x", line)
    return line


def _host_allowed(host: str) -> bool:
    # A BRACED host is reported, and that is a deliberate false positive.
    #
    # `https://{node.host}/api` (a URL template) and `https://{db.internal}/api`  (pub-lint:allow)
    # (a wrapped internal hostname) are syntactically identical, so no rule can
    # keep one and drop the other. Round 3 called the first a false positive and
    # this function briefly skipped braced hosts; round 4 then showed what that
    # bought — every non-public TLD (.internal, .local, .lan, .cluster) sailed
    # through BOTH gates, because _DNS only knows ten public TLDs and _URL had
    # just been told to look away. A silent leak is strictly worse than a finding
    # a human clears with a pragma, so the template pays the tax.
    #
    # This is the same call already made for `{config.service.io}` (pub-lint:allow): a property
    # path that ends in a real TLD stays a finding. Consistency matters here —
    # the two cases are one question, and answering it differently in two places
    # is how a boundary rots.
    if "(" in host:
        return True     # residual unbalanced call syntax (e.g. headers.get() — code,
                        # not a coordinate; balanced placeholders were masked already
    h = host.lower().rstrip(".")
    if h in _ALLOWED_HOSTS or _IPV4.fullmatch(h) or ":" in h:
        return True if h in _ALLOWED_HOSTS else False   # bare IPs judged by the ip rule
    if "." not in h:
        return True                                     # single-label (unix sockets, vars)
    return any(h == s.lstrip(".") or h.endswith(s if s.startswith(".") else "." + s)
               for s in _ALLOWED_HOST_SUFFIXES)


def scan_text(text: str, path: str = "<text>") -> list[dict]:
    findings: list[dict] = []

    def add(rule: str, line_no: int, match: str) -> None:
        findings.append({"rule": rule, "path": path, "line": line_no, "match": match})

    for i, line in enumerate(text.splitlines(), 1):
        if PRAGMA in line:
            continue
        line = _mask_placeholders(line)
        for m in _URL.finditer(line):
            if not _host_allowed(m.group(1)):
                add("url-host", i, m.group(1))
        for m in _DSN_HOST.finditer(line):
            h = m.group(1)
            if h.lower() not in _ALLOWED_HOSTS and not _host_allowed(h):
                add("url-host", i, f"host={h}")
        for m in _IPV4.finditer(line):
            ip = m.group(1)
            if all(int(o) <= 255 for o in ip.split(".")):
                if _DOC_V4.match(ip) or _ALLOWED_V4.match(ip):
                    continue
                add("ip", i, ip)
        for m in _IPV6_BAD.finditer(line):
            add("ip", i, m.group(1))
        for m in _DNS.finditer(line):
            if not _host_allowed(m.group(1)):
                add("dns", i, m.group(1))
        for m in _HOME.finditer(line):
            add("home-path", i, m.group(1))
        for m in _EMAIL.finditer(line):
            e = m.group(0)
            if not any(e.lower().endswith(s) or e.lower() == s
                       for s in _ALLOWED_EMAIL_SUFFIXES):
                add("email", i, e)
    return findings


def _tracked_files(root: str) -> list[str]:
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                         capture_output=True, check=True)
    return [f for f in out.stdout.decode().split("\0") if f]


def finding_key(f: dict) -> str:
    """Baseline identity of a finding. Deliberately path+rule+match and NOT
    line number: a reviewed exception survives the file being reformatted, but
    a DIFFERENT value at the same path is a new key and fails again (so a
    fixture re-captured with a real operator path is caught, not laundered)."""
    return f"{f['path']}::{f['rule']}::{f['match']}"


_key = finding_key   # back-compat alias for in-repo callers


def load_baseline(root: str) -> set[str]:
    """Reviewed, accepted findings for the tree at `root`.

    Read from the tree being scanned rather than the caller's cwd, so the
    EXPORT gate vouches for the baseline it actually publishes — an exception
    can never be granted by a file that does not ship with the content."""
    path = os.path.join(root, BASELINE)
    if not os.path.exists(path):
        return set()
    return set(json.load(open(path)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--update-baseline", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    findings: list[dict] = []
    for rel in _tracked_files(root):
        if any(rel.startswith(d) for d in _SKIP_DIRS) or rel == BASELINE:
            continue
        full = os.path.join(root, rel)
        try:
            raw = open(full, "rb").read()
        except OSError:
            continue
        if _BINARY_HINT in raw[:4096]:
            continue
        findings.extend(scan_text(raw.decode("utf-8", errors="replace"), rel))

    baseline_path = os.path.join(root, BASELINE)
    known = load_baseline(root)
    new = [f for f in findings if finding_key(f) not in known]

    if args.update_baseline:
        # This scan sees HEAD; the export gate sees EVERY blob in the published
        # history. A finding fixed at HEAD therefore stays live for the export
        # forever (history is immutable), so regenerating purely from HEAD would
        # silently drop the entry that keeps the export buildable — and the
        # breakage lands later, on whoever next tries to publish.
        #
        # This is append-only ON PURPOSE, and there is deliberately no --prune:
        # any HEAD-based prune is in direct contradiction with a full-history
        # gate (round 2 finding f-2 built the case — rename a file, prune, and
        # the pre-rename blob fails the export forever). Dropping an entry is a
        # hand edit to a small JSON file, which is reviewable as a diff. That is
        # the right amount of friction for removing a publication exception.
        os.makedirs(os.path.dirname(baseline_path), exist_ok=True)
        fresh = {finding_key(f) for f in findings}
        unseen = sorted(known - fresh)
        keep = fresh | set(unseen)
        json.dump(sorted(keep), open(baseline_path, "w"), indent=1)
        print(f"[pub-lint] baseline rewritten: {len(findings)} finding(s) at HEAD, "
              f"{len(keep)} entries recorded")
        for k in unseen:
            print(f"[pub-lint] retained (not at HEAD; may still be live in "
                  f"published history — remove by hand if truly stale): {k}")
        return 0

    for f in new:
        print(f"[pub-lint] NEW {f['rule']}: {f['path']}:{f['line']}  {f['match']}")
    print(f"[pub-lint] {len(new)} new, {len(findings) - len(new)} baselined, "
          f"{len(known)} baseline entries")
    if new:
        print("[pub-lint] FAIL — review each finding: replace with a synthetic "
              "placeholder, add a pub-lint:allow pragma for a deliberate example, "
              "or --update-baseline after human review.")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
