"""Gate-1 publication linter tests (#152). Prohibited fixtures are ASSEMBLED
AT RUNTIME (join/concat) so this tracked file never itself contains a
contiguous prohibited token — the linter scanning the repo must stay clean
while these tests exercise every rule positively."""
from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

import lint_publication_boundary as lint  # noqa: E402
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


# --- private-repo: an allowed HOST can still carry an unreachable PATH -------
# Measured 2026-08-20: one such link shipped in the live public mirror and 404s
# for every anonymous reader. `github.com` is allowlisted as a host, so none of
# the rules above ever looked at the path. The URLs below are assembled from
# parts so this file does not itself carry the coordinate it is testing.

_OWNER = "unagi" + "kudari"
_PRIVATE = _OWNER + "/kawa"


def _flagged(text):  # type: ignore[no-untyped-def]
    return [f["match"] for f in scan_text(text) if f["rule"] == "private-repo"]


def test_a_url_into_the_private_repo_is_a_finding():  # type: ignore[no-untyped-def]
    assert _flagged(f"see https://github.com/{_PRIVATE}/issues/122") == [_PRIVATE]


def test_the_clone_and_raw_forms_are_caught_too():  # type: ignore[no-untyped-def]
    """`.git` and raw.githubusercontent are the same unreachable path wearing
    different syntax — the earlier url-host rule waved both through."""
    assert _flagged(f"git clone https://github.com/{_PRIVATE}.git") == [_PRIVATE]
    assert _flagged(f"https://raw.githubusercontent.com/{_PRIVATE}/main/README.md") \
        == [_PRIVATE]


def test_the_public_projection_is_not_a_finding():  # type: ignore[no-untyped-def]
    """The rule must not flag the one namespace link a reader CAN open, or the
    landing page and README become unwritable."""
    assert _flagged(f"https://github.com/{_OWNER}/yukukawa/issues") == []
    assert _flagged(f"https://github.com/{_OWNER}/yukukawa.dev") == []


def test_third_party_repositories_stay_legal():  # type: ignore[no-untyped-def]
    """Scoping to our OWN namespace is what makes the rule maintenance-free —
    it must not become a general ban on linking to GitHub."""
    assert _flagged("https://github.com/psf/requests/issues/1") == []
    assert _flagged("https://github.com/owner/repo") == []


def test_the_provenance_COORDINATE_form_is_deliberately_legal():  # type: ignore[no-untyped-def]
    """Publication condition 7 keeps internal events pinned to private
    coordinates. The rule targets LINKABILITY, not mention: `github:owner/repo#N`
    invites no click and resolves nothing, so flagging it would make the
    sanctioned spelling unwritable and push authors toward the URL form —
    exactly backwards."""
    assert _flagged(f"provenance: github:{_PRIVATE}#122") == []
    assert _flagged(f"tracked in {_PRIVATE} PR #109") == []


def test_a_trailing_dot_host_is_still_a_working_link():  # type: ignore[no-untyped-def]
    """Round-1 bypass (a): the root label is legal FQDN syntax that browsers
    and DNS resolve identically, and the first regex required `/` immediately
    after the host — so one extra character reached the reader as a working
    link into the private repo."""
    assert _flagged(f"https://github.com./{_PRIVATE}/issues/122") == [_PRIVATE]


def test_the_ssh_clone_form_is_an_actionable_coordinate_too():  # type: ignore[no-untyped-def]
    """Not browser-clickable, but a clone instruction the reader cannot carry
    out is the same broken promise wearing different syntax — which is why the
    rule is scoped to ACTIONABLE coordinates rather than to hyperlinks."""
    assert _flagged(f"git clone git@github.com:{_PRIVATE}.git") == [_PRIVATE]


def test_masking_does_not_invent_a_private_repo_out_of_the_public_one():  # type: ignore[no-untyped-def]
    """The rule reads the RAW line, and this is why.

    Round 1 reported the opposite risk — that masking `{owner}` to `x` lets a
    private link escape. Measured, it does not: that shape misses either way,
    and it is not a link anyone can follow. What masking DOES do is rewrite
    `yukukawa{n}` to `yukukawax`, which is not the public repo, so a legal URL
    pattern in a doc becomes a false finding against the boundary."""
    assert _flagged(f"https://github.com/{_OWNER}/yukukawa{{n}}") == []
    assert _flagged("https://github.com/{owner}/kawa") == []       # still not a link
    assert _flagged(f"see https://github.com/{_PRIVATE}/pull/{{n}}") == [_PRIVATE]


def test_the_rule_survives_an_owner_with_different_case():  # type: ignore[no-untyped-def]
    assert _flagged(f"https://GitHub.com/{_OWNER.upper()}/Kawa/pull/9") != []


def test_the_ssh_clone_user_is_not_an_email_leak():  # type: ignore[no-untyped-def]
    """`git@github.com` is the universal SSH user for every GitHub clone URL.
    Documenting a clone command must not read as an operator address — and the
    exemption is EXACT, not suffix-matched, or it would admit any address
    ending in the same characters."""
    assert "email" not in _rules("git clone git@github.com:owner/repo.git")
    assert "email" in _rules("contact someone-git@github.com")   # pub-lint:allow


def test_an_explicit_port_does_not_eat_the_owner_slot():  # type: ignore[no-untyped-def]
    """Round-2 residual, introduced by round 1's own widening: allowing `:` as
    a separator (for the SSH clone form) let `github.com:443/owner/repo` parse
    the PORT as the owner, so the real owner was never checked. A browse URL
    with an explicit port is unusual but entirely working."""
    assert _flagged(f"https://github.com:443/{_PRIVATE}/issues/1") == [_PRIVATE]
    assert _flagged(f"https://github.com:443/{_OWNER}/yukukawa") == []


# --- baseline identity: a path exception and a blob exception differ ---------

def test_a_blob_exception_cannot_travel_to_new_content():  # type: ignore[no-untyped-def]
    """The reason history findings are keyed by blob, not by path.

    2026-08-20: the private-repo rule found ~183 links into the private repo
    across 13 superseded README blobs, all already published and unrepairable
    (history rewrite is prohibited). Accepting them under
    `README.md::private-repo::<owner>/<repo>` would ALSO have accepted the same
    link reappearing in the README tomorrow — a permanent blind spot on the
    most-edited file in the tree. A blob id is content and cannot be reissued
    for different bytes, so an exemption granted there stays there."""
    f = {"path": "README.md", "rule": "private-repo", "match": "owner/repo"}
    blob = lint.history_key(f, "a" * 40)
    assert lint.history_key(f, "b" * 40) != blob       # per-blob, not per-path

    # the property that matters: a baseline holding ONLY the blob key must
    # leave a finding at the same path, same rule, same match still live
    known = {blob}
    assert lint.finding_key(f) not in known


def test_the_baseline_carries_its_own_reason(tmp_path):  # type: ignore[no-untyped-def]
    """An entry records accepted PUBLIC EXPOSURE. A bare key does not say what
    was decided or why, and the reason is what the next reviewer needs."""
    import json
    import os
    reg = tmp_path / "registry"
    reg.mkdir()
    key = "a.md::private-repo::owner/repo"
    (reg / "publication-baseline.json").write_text(json.dumps({key: "reviewed: X"}))
    assert lint.load_baseline(str(tmp_path)) == {key}
    assert lint.load_baseline_reasons(str(tmp_path))[key] == "reviewed: X"


def test_the_legacy_list_baseline_still_loads(tmp_path):  # type: ignore[no-untyped-def]
    """An older tree must stay scannable — the export gate reads the baseline
    out of the tree it is publishing, which may predate this format."""
    import json
    reg = tmp_path / "registry"
    reg.mkdir()
    (reg / "publication-baseline.json").write_text(json.dumps(["a.md::ip::192.0.2.1"]))
    assert lint.load_baseline(str(tmp_path)) == {"a.md::ip::192.0.2.1"}
    assert lint.load_baseline_reasons(str(tmp_path))["a.md::ip::192.0.2.1"] == ""


def test_every_baseline_entry_says_why():  # type: ignore[no-untyped-def]
    """The register of accepted exposure must not accumulate silent entries."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    reasons = lint.load_baseline_reasons(str(repo))
    silent = sorted(k for k, why in reasons.items() if not why.strip())
    assert silent == [], f"baseline entries with no stated reason: {silent}"


def test_the_tracked_tree_has_no_unreviewed_findings():  # type: ignore[no-untyped-def]
    """Run the gate itself, not just its rules.

    CI runs this linter and its path filter covers tests/** — so a lint
    failure is never silent. But the feedback arrives at PR time, and on
    2026-08-20 a fixture IP added to THIS file failed the gate while the whole
    pytest suite passed, which is how it reached a commit. Same check, an
    order of magnitude sooner.

    It calls `scan_tree`, the same function `main()` uses, so the suite and CI
    cannot come to disagree about what a scan is.

    Caveat: `scan_tree` reads the WORKING TREE. An uncommitted edit failing
    the suite is the point — that is the incident above. The converse is a
    real if unusual gap: stage a finding, then revert the file on disk without
    re-adding, and the index still carries what `git commit` would ship while
    this reads the clean copy."""
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    known = lint.load_baseline(str(repo))
    new = [f"{f['rule']}: {f['path']}:{f['line']} {f['match']}"
           for f in lint.scan_tree(str(repo)) if lint.finding_key(f) not in known]
    assert new == [], "unreviewed publication findings at HEAD:\n  " + "\n  ".join(new)


def test_scan_tree_skips_binaries_and_the_register_itself(tmp_path):  # type: ignore[no-untyped-def]
    """Two skips that no in-repo test can exercise: this tree tracks no binary
    file today, and the baseline is skipped because scanning the register of
    findings would report its own quoted contents. Both are properties of
    `scan_tree`, so they get a tree of their own."""
    import json
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    payload = b"host=vault.corp.io\n"                  # pub-lint:allow
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00" + payload)
    (tmp_path / "notes.md").write_bytes(payload)
    reg = tmp_path / "registry"
    reg.mkdir()
    (reg / "publication-baseline.json").write_text(
        json.dumps({"notes.md::url-host::vault.corp.io": "x"}))   # pub-lint:allow
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)

    paths = {f["path"] for f in lint.scan_tree(str(tmp_path))}
    assert paths == {"notes.md"}          # the text file, and only it
    assert "logo.png" not in paths        # a binary is not scanned as text
    assert lint.BASELINE not in paths     # the register never scans itself
