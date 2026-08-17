"""w-pub2 export tests: identity rewrite, provenance trailers, verbatim blob
survival, determinism, and the full-history gate — against a synthetic repo
built fresh per test (the export tool must never touch the source)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

import export_public_mirror as ex  # noqa: E402

_PUB = ("Public Name", "1234+public@users.noreply.github.com")
# adversarial blob: author-shaped line + a `data N` line inside file CONTENT —
# the rewriter must copy it byte-identically (never pattern-match payloads)
# allowlisted address on purpose: this blob must SURVIVE the gate while still
# attacking the stream parser with author-shaped and data-shaped lines
_TRICKY = ("author Trap Person <trap@example.com> 123 +0000\n"
           "data 5\nhello\n")


def _git(cwd, *args, **kw):  # type: ignore[no-untyped-def]
    return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, **kw)


@pytest.fixture()
def source(tmp_path):  # type: ignore[no-untyped-def]
    src = str(tmp_path / "src")
    os.makedirs(src)
    _git(src, "init", "-q", "-b", "main")
    _git(src, "config", "user.name", "Private Dev")
    _git(src, "config", "user.email", "personal@example.com")  # stands in for a real address
    open(os.path.join(src, "tricky.txt"), "w").write(_TRICKY)
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "first: multiline\n\nbody line\n")
    open(os.path.join(src, "second.txt"), "w").write("clean content\n")
    _git(src, "add", "-A")
    _git(src, "commit", "-qm", "second")
    return src


def _shas(cwd):  # type: ignore[no-untyped-def]
    return _git(cwd, "rev-list", "--reverse", "HEAD").stdout.decode().split()


def test_export_rewrites_identity_and_maps_provenance(source, tmp_path):  # type: ignore[no-untyped-def]
    dest = str(tmp_path / "pub")
    ex.export(source, dest, "main", *_PUB)
    idents = set(_git(dest, "log", "--format=%an <%ae> %cn <%ce>").stdout.decode().splitlines())
    assert idents == {"Public Name <1234+public@users.noreply.github.com> "
                      "Public Name <1234+public@users.noreply.github.com>"}
    # Source-Commit trailers map 1:1, in order, to the private shas
    trailers = _git(dest, "log", "--reverse", "--format=%(trailers:key=Source-Commit,valueonly)"
                    ).stdout.decode().split()
    assert trailers == _shas(source)


def test_blob_contents_survive_byte_identical(source, tmp_path):  # type: ignore[no-untyped-def]
    dest = str(tmp_path / "pub")
    ex.export(source, dest, "main", *_PUB)
    exported = open(os.path.join(dest, "tricky.txt")).read()
    assert exported == _TRICKY                       # payloads never rewritten or trailered


def test_export_is_deterministic(source, tmp_path):  # type: ignore[no-untyped-def]
    a = ex.export(source, str(tmp_path / "a"), "main", *_PUB)
    b = ex.export(source, str(tmp_path / "b"), "main", *_PUB)
    assert a == b                                    # same source ref -> identical history


def test_gate_failure_deletes_the_export(source, tmp_path):  # type: ignore[no-untyped-def]
    leaky = "server " + ".".join(["192", "168", "44", "7"]) + "\n"
    open(os.path.join(source, "leak.txt"), "w").write(leaky)
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "oops")
    dest = str(tmp_path / "pub")
    with pytest.raises(SystemExit, match="GATE FAILED"):
        ex.export(source, dest, "main", *_PUB)
    assert not os.path.exists(dest)                  # failed exports leave nothing behind


def test_gate_scans_history_not_just_head(source, tmp_path):  # type: ignore[no-untyped-def]
    leaky = "server " + ".".join(["10", "1", "2", "3"]) + "\n"
    open(os.path.join(source, "leak.txt"), "w").write(leaky)
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "leak lands")
    os.remove(os.path.join(source, "leak.txt"))
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "leak removed from HEAD but not from history")
    with pytest.raises(SystemExit, match="GATE FAILED"):
        ex.export(source, str(tmp_path / "pub"), "main", *_PUB)


def test_private_source_is_never_touched(source, tmp_path):  # type: ignore[no-untyped-def]
    before = _shas(source)
    ex.export(source, str(tmp_path / "pub"), "main", *_PUB)
    assert _shas(source) == before                   # condition 1: private history untouched
