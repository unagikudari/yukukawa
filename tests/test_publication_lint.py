"""Gate-1 publication linter tests (#152). Prohibited fixtures are ASSEMBLED
AT RUNTIME (join/concat) so this tracked file never itself contains a
contiguous prohibited token — the linter scanning the repo must stay clean
while these tests exercise every rule positively."""
from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

from lint_publication_boundary import PRAGMA, scan_text  # noqa: E402


def _rules(text: str) -> list[str]:
    return [f["rule"] for f in scan_text(text)]


# --- prohibited side: every class detects (fixtures assembled at runtime) ---

def test_private_ipv4_detected():  # type: ignore[no-untyped-def]
    assert _rules("db at " + ".".join(["192", "168", "1", "5"])) == ["ip"]
    assert _rules("api " + ".".join(["10", "0", "3", "7"])) == ["ip"]
    assert _rules("public " + ".".join(["8", "8", "8", "8"])) == ["ip"]  # non-doc public: also out


def test_ula_and_linklocal_ipv6_detected():  # type: ignore[no-untyped-def]
    assert _rules("addr " + ":".join(["fd7a", "115c", "", "1"])) == ["ip"]
    assert _rules("ll " + ":".join(["fe80", "", "abcd"])) == ["ip"]


def test_operator_dns_detected_allowlist_passes():  # type: ignore[no-untyped-def]
    tailnetish = ".".join(["node1", "tail0123", "net"])
    assert _rules("host " + tailnetish) == ["dns"]
    assert _rules("see https://github.com/owner/repo and example.invalid") == []
    assert _rules("standards: www.w3.org json-schema.org") == []


def test_url_and_dsn_hosts_detected():  # type: ignore[no-untyped-def]
    url = "https://" + ".".join(["internal", "corp", "io"]) + "/x"
    got = _rules(url)
    assert "url-host" in got                             # dns rule may also fire — fine
    dsn = "dbname=kawa host=" + ".".join(["db1", "corp", "io"])
    assert "url-host" in _rules(dsn)
    assert _rules("dbname=kawa host=localhost") == []
    assert _rules("http://127.0.0.1:8099/") == []        # loopback identifies nothing


def test_home_paths_detected():  # type: ignore[no-untyped-def]
    assert _rules("WorkingDirectory=" + "/".join(["", "home", "alice"])) == ["home-path"]
    assert _rules("mac " + "/".join(["", "Users", "alice"])) == ["home-path"]
    assert _rules("WorkingDirectory=%h/kawa") == []      # portable form stays legal


def test_emails_detected_noreply_passes():  # type: ignore[no-untyped-def]
    personal = "someone@" + ".".join(["gmail", "com"])
    got = _rules("contact " + personal)
    assert "email" in got
    assert _rules("1234+user@users.noreply.github.com") == []


def test_code_templates_are_not_coordinates():  # type: ignore[no-untyped-def]
    assert _rules('f"file://{os.path.abspath(p)}"') == []
    assert _rules("host=self.headers.get('Host')") == []


def test_pragma_line_is_skipped():  # type: ignore[no-untyped-def]
    bad = "ip " + ".".join(["192", "168", "0", "1"])
    assert _rules(bad + "   # " + PRAGMA) == []


def test_bare_nicknames_stay_legal():  # type: ignore[no-untyped-def]
    # the dogfood-evidence exception: nicknames are NOT the prohibited side
    assert _rules("nodes: alpha bravo; lane bravo-cc-primary; KAWA_NODE=bravo") == []


def test_placeholder_is_masked_not_skipped():  # type: ignore[no-untyped-def]
    # review (b): a template inside a real FQDN must not launder the suffix
    laundered = "https://foo.{ENV}." + ".".join(["corp", "io"]) + "/x"  # pub-lint:allow
    assert "url-host" in _rules(laundered)
    assert _rules('f"file://{os.path.abspath(p)}"') == []   # pure placeholder stays clean
