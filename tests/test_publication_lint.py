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


# --- #201: placeholder masking must not eat DATA -----------------------------
# The rule masks TEMPLATE syntax. It used to mask any brace pair, and because
# the mask runs to a fixpoint, the innermost objects of a JSON document
# collapsed outward until a one-line file was a single `x` and scanned clean.
# These pin the distinction from both sides.

_REAL_CAPTURE = os.path.join(_REPO, "tests", "fixtures", "herdr", "workspace_create.json")


def _operator_shaped() -> tuple[str, str]:
    """An operator home path and an FQDN, assembled at runtime so this tracked
    test file never itself carries a contiguous prohibited token."""
    return "/".join(["", "home", "alice"]), ".".join(["node1", "tail0123", "net"])


def test_single_line_json_is_scanned_not_masked_away():  # type: ignore[no-untyped-def]
    """Built from a REAL captured runtime response (tests/fixtures/herdr/,
    Herdr 0.8.0, 2026-08-18) with its scrubbed placeholder swapped back for an
    operator-shaped value — the exact shape that reported 0 findings."""
    home, fqdn = _operator_shaped()
    captured = open(_REAL_CAPTURE, encoding="utf-8").read()
    assert "\n" not in captured.strip(), "fixture must stay one line for this regression"
    scrubbed = "/".join(["", "home", "node"])          # assembled: keep this file clean
    leaky = captured.replace(scrubbed, home).replace("term_", fqdn + "/term_")
    rules = _rules(leaky)
    assert "home-path" in rules, "one-line JSON must not be masked away"
    assert "dns" in rules


def test_masking_survives_nesting_and_non_json_containers():  # type: ignore[no-untyped-def]
    """The hole was never JSON-specific: any brace-balanced one-liner (a dict
    literal in a .py/.md) collapsed the same way."""
    home, _ = _operator_shaped()
    assert "home-path" in _rules('{"a":{"b":{"cwd":"' + home + '"}}}')      # deep nesting
    assert "home-path" in _rules('payload = {"path": "' + home + '"}')      # py dict literal
    assert "home-path" in _rules('const c = {path: "' + home + '"};')       # js object


def test_template_tokens_still_mask():  # type: ignore[no-untyped-def]
    """The narrowed rule must not regress the false-positive suppression it
    exists for: a template is still masked, and masking still refuses to
    launder the surrounding coordinate."""
    assert _rules('cmd = "$(hostname -f)"') == []
    assert _rules('url = "${KAWA_ENDPOINT}/x"') == []
    templated = "/".join(["", "home", "{user}"]) + "/kawa"
    assert _rules(f'p = "{templated}"') == ["home-path"]        # masked, still caught
    assert _rules("run $(echo $(hostname))") == []              # nested substitution


def test_substitution_syntax_does_not_hide_its_payload():  # type: ignore[no-untyped-def]
    """`${...}` and `$(...)` were the SAME greedy bug as the braces, in other
    syntax: a shell default value and a command argument are DATA, and were
    masked away with the substitution name. Adversarial review of the first
    cut of this fix (PR #207 round 1) constructed all three."""
    home, fqdn = _operator_shaped()
    ip = ".".join(["192", "168", "1", "50"])

    assert "home-path" in _rules('D="${DIR:-' + home + '/secret}"')     # default value
    assert "dns" in _rules('u="${API_URL:-https://' + fqdn + '/api}"')  # default value
    assert "ip" in _rules('c="$(curl http://' + ip + '/token)"')        # command argument
    assert "dns" in _rules('h="$(echo ' + fqdn + ')"')                  # bare argument


def test_brace_token_cannot_wrap_a_hostname():  # type: ignore[no-untyped-def]
    """A DOTTED brace token is structurally identical to an FQDN, so allowing
    dots let a brace-wrapped hostname mask itself away. Round 2 of the #207
    review built every case below; each returned 0 findings beforehand."""
    _, fqdn = _operator_shaped()
    corp = ".".join(["internal", "corp", "io"])

    # Detection, not which rule fires: a braced host draws BOTH a url-host
    # finding (see test_braced_host_is_reported_even_though_it_may_be_a_template)
    # and, when the wrapped text is a real FQDN, a dns one. Either proves the
    # coordinate is no longer hidden, which is what this test is about.
    assert _rules("target = {" + fqdn + "}")           # bare brace wrap
    assert _rules("${" + corp + "}")                   # $-prefixed, dotted
    assert _rules("https://${" + corp + "}/api")       # inside a URL
    assert _rules("host={" + corp + "}")               # inside a DSN


def test_single_segment_templates_still_mask():  # type: ignore[no-untyped-def]
    """Dropping dots costs nothing: masking only matters when a placeholder is
    embedded IN a coordinate, and there the token is single-segment. A dotted
    template left unmasked raises no finding of its own."""
    embedded = "https://foo.{ENV}." + ".".join(["corp", "io"])   # pub-lint:allow
    assert "url-host" in _rules(embedded)
    assert _rules("cfg = {node.name} and {node-id}") == []     # unmasked, harmless
    assert _rules('p = "{host}:{port}"') == []


def test_leading_placeholder_does_not_exempt_the_coordinate():  # type: ignore[no-untyped-def]
    """`_DNS` required a two-character FIRST label, so any coordinate whose
    leading label was a template scanned clean once masking rewrote it to one
    character. The earlier test only covered `foo.{ENV}.corp.io` (pub-lint:allow), where the
    prefix happened to be long enough. Round 3 of the review found it."""
    corp = ".".join(["corp", "io"])
    tailnetish = ".".join(["tail0123", "net"])

    assert "dns" in _rules("server = {host}." + corp)              # pub-lint:allow
    assert "dns" in _rules('connect("{cluster}.' + tailnetish + '")')  # pub-lint:allow
    assert "dns" in _rules("ping ${NODE}." + ".".join(["internal", "net"]))  # pub-lint:allow
    assert "dns" in _rules("{a}.{b}." + corp)                      # pub-lint:allow
    assert "dns" in _rules("a." + corp)      # a 1-char first label is a real FQDN


def test_braced_host_is_reported_even_though_it_may_be_a_template():
    # type: ignore[no-untyped-def]
    """A braced host is a DELIBERATE false positive.

    `https://{node.host}/api` (a template) and `https://{db.internal}/api` (a  (pub-lint:allow)
    wrapped internal hostname) are syntactically identical, so no rule keeps one
    and drops the other. Skipping braced hosts was tried; round 4 showed the
    price: every non-public TLD sailed through BOTH gates, because `_DNS` knows
    only ten public TLDs and `_URL` had been told to look away. A finding a
    human clears with a pragma beats a silent leak."""
    # assembled at runtime so this tracked file stays clean (see module docstring)
    privates = ["{" + ".".join(p) + "}" for p in
                (("db", "internal"), ("redis", "local"),
                 ("node1", "lan"), ("master", "cluster"))]
    for private in privates:
        assert "url-host" in _rules("https://" + private + "/api"), private
        assert "url-host" in _rules("host=" + private), private
    assert "url-host" in _rules("postgresql://u:p@" + privates[2] + ":5432/d")

    # the tax: an honest template in host position is reported too
    template = "{" + ".".join(["node", "host"]) + "}"
    assert "url-host" in _rules("https://" + template + "/api")

    wrapped = ".".join(["internal", "corp", "io"])
    assert _rules("https://{" + wrapped + "}/api")
    assert _rules("host={" + wrapped + "}")


def test_query_and_fragment_terminate_the_host():  # type: ignore[no-untyped-def]
    """`?` and `#` end the host as surely as `/`. Without them a path-less URL
    swallowed its query, so the match reported was meaningless rather than the
    coordinate (round 4)."""
    host = ".".join(["api", "internal"])       # assembled: keep this file clean
    for url in (f"https://{host}?query={{token}}", f"https://{host}#frag"):
        assert [f["match"] for f in scan_text(url)
                if f["rule"] == "url-host"] == [host], url


def test_hyphenated_template_names_mask():  # type: ignore[no-untyped-def]
    """A hostname needs a DOT, so a hyphen cannot wrap an FQDN. Leaving `-` out
    of the brace form only meant `{app-id}` survived unmasked into host
    position (round 4)."""
    assert _rules("p = {node-1}") == []
    assert _rules("cfg = {app-id} {a-b-c}") == []
    assert "dns" in _rules("https://{app-id}." + ".".join(["corp", "io"]))


def test_home_path_account_is_case_insensitive():  # type: ignore[no-untyped-def]
    """/Users/ already accepted a capitalised account; /home/ did not, so a
    Linux account starting with a capital slipped through (round 2 finding 5)."""
    assert _rules("WorkingDirectory=" + "/".join(["", "home", "Alice"])) == ["home-path"]
