"""Launch environment allowlist (#200 rev 3 §REAL-5).

An inherited environment is a credential-bearing surface: GitHub tokens,
SSH agent sockets, cloud creds and editor auth all live there. A launched
agent gets a CONSTRUCTED environment instead — two allowlisted tiers and
nothing else:

  * OS-required — without these the agent or the tools it shells out to die
    or start asking a TTY questions (round-2 F4 measured the gap: USER /
    LOGNAME for git and permission checks, TMPDIR/TMP for runtime temp
    files, the cert-bundle vars for custom-CA networks).
  * Kawa-required — how the launched agent finds the log it must pull from.

Agent credentials deliberately do NOT travel here: claude and codex read
their own config files (`~/.claude`, `~/.codex`) under the launched user,
and git auth resolves through that user's credential helper. Passing
`SSH_AUTH_SOCK` / `GITHUB_TOKEN` through would hand every launched pane the
operator's full push authority — the plan refuses that by construction, and
`build_env` has no parameter that could re-enable it.
"""
from __future__ import annotations

import os

_OS_REQUIRED = ("HOME", "PATH", "TERM", "USER", "LOGNAME", "TMPDIR", "TMP",
                "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
                "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")
_KAWA_REQUIRED = ("KAWA_DSN", "KAWA_NODE")
_LOCALE_PREFIX = "LC_"

# names that must never be forwarded even if some future tier lists them
DENIED = frozenset({"SSH_AUTH_SOCK", "GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN",
                    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                    "GOOGLE_APPLICATION_CREDENTIALS", "ANTHROPIC_API_KEY",
                    "OPENAI_API_KEY", "KAWA_API_KEY", "BROKER_API_KEY"})


def build_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """The launched runtime's complete environment. Absent names are simply
    absent — no invented defaults (a wrong HOME is worse than no HOME)."""
    src = os.environ if base is None else base
    out: dict[str, str] = {}
    for name in _OS_REQUIRED + _KAWA_REQUIRED:
        if name in src and name not in DENIED:
            out[name] = src[name]
    for name, value in src.items():
        if name.startswith(_LOCALE_PREFIX) and name not in DENIED:
            out[name] = value
    return out
