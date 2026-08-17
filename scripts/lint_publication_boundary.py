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
BASELINE = os.path.join("registry", "publication-baseline.json")

_DOC_V4 = re.compile(r"^(192\.0\.2|198\.51\.100|203\.0\.113)\.")
_ALLOWED_V4 = re.compile(r"^(127\.|0\.0\.0\.0$|255\.255\.255\.255$)")
_IPV4 = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3})\b")
_IPV6_BAD = re.compile(r"\b(f[cd][0-9a-f]{2}:[0-9a-f:]{2,}|fe80:[0-9a-f:]{2,})", re.I)
_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^/\s\"'<>@]*@)?([^/\s\"'<>:]+)", re.I)
_DSN_HOST = re.compile(r"\bhost=([^\s\"']+)")
_DNS = re.compile(r"\b([a-z0-9][a-z0-9-]{1,62}(?:\.[a-z0-9][a-z0-9-]{0,62})+"
                  r"\.(?:net|com|org|io|dev|app|ai|cloud|sh|jp))\b")
_HOME = re.compile(r"(/home/[a-z0-9][a-z0-9_-]*|/Users/[A-Za-z0-9][A-Za-z0-9_-]*)")
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


_PLACEHOLDER = re.compile(r"\{[^{}]*\}|\$\([^()]*\)|\$\{[^{}]*\}")


def _mask_placeholders(line: str) -> str:
    """Review (b): placeholders are MASKED to 'x', never skipped wholesale —
    `foo.{ENV}.corp.io` must still scan as masked and be caught. (pub-lint:allow)"""
    prev = None
    while prev != line:
        prev, line = line, _PLACEHOLDER.sub("x", line)
    return line


def _host_allowed(host: str) -> bool:
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


def _key(f: dict) -> str:
    return f"{f['path']}::{f['rule']}::{f['match']}"


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
    known: set[str] = set()
    if os.path.exists(baseline_path):
        known = set(json.load(open(baseline_path)))
    new = [f for f in findings if _key(f) not in known]

    if args.update_baseline:
        os.makedirs(os.path.dirname(baseline_path), exist_ok=True)
        json.dump(sorted({_key(f) for f in findings}), open(baseline_path, "w"), indent=1)
        print(f"[pub-lint] baseline rewritten: {len(findings)} finding(s) recorded")
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
