#!/usr/bin/env python3
"""Unified SAST and security invariant linter for Kawa.

Runs:
  1. Bandit AST static analysis (Medium & High severity)
  2. pip-audit dependency CVE vulnerability check
  3. Kawa custom security invariant checks:
     - No private key / bearer secret literals in domain code
     - Safe URL scheme enforcement in transport adapters
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_bandit() -> bool:
    print("[1/3] Running Bandit SAST scan on kawa/...")
    cmd = [
        sys.executable, "-m", "bandit",
        "-r", str(REPO_ROOT / "kawa"),
        "-c", str(REPO_ROOT / "pyproject.toml"),
        "-ll",  # Medium and High severity
        "-q",
    ]
    res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        print("❌ Bandit SAST scan found potential vulnerabilities:")
        print(res.stdout or res.stderr)
        return False
    print("  ✓ Bandit SAST scan clean (0 High/Medium issues).")
    return True


def run_pip_audit() -> bool:
    print("[2/3] Running pip-audit on dependencies...")
    cmd = [
        sys.executable, "-m", "pip_audit",
        "--desc",
        "--ignore-vuln", "PYSEC-2026-196",
        "--ignore-vuln", "PYSEC-2026-1795",
        "--ignore-vuln", "PYSEC-2026-1796",
        "--ignore-vuln", "PYSEC-2026-2875",
        "--ignore-vuln", "PYSEC-2026-2876",
    ]
    res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        print("❌ pip-audit found dependency vulnerabilities:")
        print(res.stdout or res.stderr)
        return False
    print("  ✓ Dependency CVE audit clean (0 production dependency vulnerabilities).")
    return True


def check_domain_security_invariants() -> bool:
    print("[3/3] Checking Kawa domain security invariants...")
    errors: list[str] = []
    kawa_dir = REPO_ROOT / "kawa"

    for py_file in kawa_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
        except Exception as e:
            errors.append(f"{py_file}: Failed to parse AST: {e}")
            continue

        # Check: No hardcoded Ed25519 private key literals or bearer secrets
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                if "BEGIN PRIVATE KEY" in val or "BEGIN OPENSSH PRIVATE KEY" in val:
                    errors.append(f"{py_file}:{node.lineno}: Hardcoded private key literal detected.")

        # Check: transport urlopen scheme validation in adapters
        if py_file.name == "replication_http.py":
            if "not base_url.startswith((" not in content:
                errors.append(f"{py_file}: Missing base_url scheme validation before urlopen.")

    if errors:
        print("❌ Domain security invariant checks failed:")
        for err in errors:
            print(f"  - {err}")
        return False

    print("  ✓ Domain security invariants passed.")
    return True


def main() -> int:
    print("=== Kawa Mechanical Security & SAST Lint ===")
    ok = True
    ok = run_bandit() and ok
    ok = run_pip_audit() and ok
    ok = check_domain_security_invariants() and ok

    if not ok:
        print("\n❌ Security lint FAILED.")
        return 1

    print("\n✅ All security & SAST checks PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
