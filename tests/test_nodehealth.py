"""Node-local resident health — the reader half of the status-file convention.

Written against the 2026-08-20 failure it exists to prevent: kawa-goatcounter
failed twice, wrote nothing anyone read, and lost a day of the site-visit
series. Every test here is a property of DELIVERY, not of formatting."""
from __future__ import annotations

import json
import os
import time

from kawa import nodehealth


def _write(d, name, payload, *, age_s: float = 0.0):  # type: ignore[no-untyped-def]
    p = os.path.join(str(d), name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    if age_s:
        t = time.time() - age_s
        os.utime(p, (t, t))
    return p


def test_healthy_node_says_nothing(tmp_path):  # type: ignore[no-untyped-def]
    """A block that prints every session is a block the reader learns to skip."""
    _write(tmp_path, "probe.status", {"ts": "t", "ok": True})
    _write(tmp_path, "goatcounter.status", {"ts": "t", "ok": True})
    assert nodehealth.scan(str(tmp_path)) == []
    assert nodehealth.render([]) == ""


def test_a_not_ok_status_reaches_the_reader(tmp_path):  # type: ignore[no-untyped-def]
    _write(tmp_path, "goatcounter.status",
           {"ts": "2026-08-20T01:36:13Z", "ok": False,
            "failed": [{"day": "2026-08-19", "error": "HTTPError: 404"}]})
    out = nodehealth.render(nodehealth.scan(str(tmp_path)))
    assert "goatcounter" in out
    assert "2026-08-19" in out and "404" in out      # WHICH day, not just "unhealthy"


def test_a_dead_process_is_caught_by_the_unit_marker(tmp_path):  # type: ignore[no-untyped-def]
    """OOM/timeout kills the collector before it can write its own status."""
    _write(tmp_path, "kawa-goatcounter.service.onfail",
           {"ts": "2026-08-20T01:36:13Z", "unit_failure": True})
    out = nodehealth.render(nodehealth.scan(str(tmp_path)))
    assert "goatcounter" in out and "kawa-goatcounter.service" in out


def test_a_later_good_run_supersedes_a_marker_systemd_cannot_clear(tmp_path):  # type: ignore[no-untyped-def]
    """Otherwise the first failure makes the block permanent, i.e. ignored."""
    _write(tmp_path, "kawa-goatcounter.service.onfail", {"ts": "old"}, age_s=600)
    _write(tmp_path, "goatcounter.status", {"ts": "new", "ok": True})
    assert nodehealth.scan(str(tmp_path)) == []


def test_an_older_good_run_does_NOT_supersede_a_newer_failure(tmp_path):  # type: ignore[no-untyped-def]
    _write(tmp_path, "goatcounter.status", {"ts": "old", "ok": True}, age_s=600)
    _write(tmp_path, "kawa-goatcounter.service.onfail", {"ts": "new"})
    assert [f["component"] for f in nodehealth.scan(str(tmp_path))] == ["goatcounter"]


def test_one_event_is_reported_once(tmp_path):  # type: ignore[no-untyped-def]
    """A day-level failure writes ok:false AND exits nonzero, which fires
    OnFailure — the same failure under the same component key twice (round-1
    review of #228, finding 3). The two-signal design is for the
    crash-before-write case, not for saying it twice."""
    _write(tmp_path, "goatcounter.status",
           {"ts": "t", "ok": False, "failed": [{"day": "2026-08-19", "error": "404"}]})
    _write(tmp_path, "kawa-goatcounter.service.onfail", {"ts": "t"})
    findings = nodehealth.scan(str(tmp_path))
    assert len(findings) == 1                       # one component, one line
    assert "2026-08-19" in findings[0]["why"]       # the detailed reason survives
    assert "unit failed" in findings[0]["why"]      # and the other is not lost


def test_the_merged_line_is_dated_by_the_more_recent_signal(tmp_path):  # type: ignore[no-untyped-def]
    """A stale status timestamp must not date a fresher unit failure — the
    reader uses that stamp to decide how old the trouble is."""
    _write(tmp_path, "goatcounter.status",
           {"ts": "2026-08-19T01:00:00Z", "ok": False, "error": "yesterday"})
    _write(tmp_path, "kawa-goatcounter.service.onfail", {"ts": "2026-08-20T09:00:00Z"})
    (finding,) = nodehealth.scan(str(tmp_path))
    assert finding["at"] == "2026-08-20T09:00:00Z"


def test_distinct_components_are_not_merged(tmp_path):  # type: ignore[no-untyped-def]
    _write(tmp_path, "goatcounter.status", {"ts": "t", "ok": False, "error": "a"})
    _write(tmp_path, "kawa-probe.service.onfail", {"ts": "t"})
    assert sorted(f["component"] for f in nodehealth.scan(str(tmp_path))) == \
        ["goatcounter", "probe"]


def test_an_unreadable_status_is_a_finding_not_a_pass(tmp_path):  # type: ignore[no-untyped-def]
    """Skipping it would make corruption indistinguishable from health."""
    with open(os.path.join(str(tmp_path), "archive.status"), "w") as f:
        f.write("{truncated")
    out = nodehealth.render(nodehealth.scan(str(tmp_path)))
    assert "archive" in out and "unreadable" in out


def test_a_collector_predating_the_ok_flag_is_not_called_broken(tmp_path):  # type: ignore[no-untyped-def]
    _write(tmp_path, "telemetry.status", {"ts": "t", "node": "n", "emitted": 11})
    assert nodehealth.scan(str(tmp_path)) == []


def test_absent_status_dir_is_silence_not_an_error(tmp_path):  # type: ignore[no-untyped-def]
    """Non-resident nodes run brief.py too; they must not see a scary block."""
    assert nodehealth.scan(str(tmp_path / "nope")) == []


def test_status_dir_honours_the_env_override(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("KAWA_STATUS_DIR", str(tmp_path))
    assert nodehealth.status_dir() == str(tmp_path)
    monkeypatch.delenv("KAWA_STATUS_DIR")
    assert nodehealth.status_dir().endswith("/.kawa/status")


def test_status_paths_resolve_through_the_fence():  # type: ignore[no-untyped-def]
    """No resident may hardcode ~/.kawa/status as a PATH it actually opens.

    Static on purpose. The first version of this guard diffed the real
    directory before and after the suite; on a node where kawa-supervisor is
    actually running, that fires every time the live supervisor writes its own
    status mid-run — a fence that accuses production of doing its job. This
    one reads the source, so it cannot race, and it fails when a hardcoded
    path is WRITTEN rather than when one happens to be exercised.

    It walks the AST rather than grepping: usage docstrings say
    `[--status-file ~/.kawa/status/archive.status]` on purpose, and a grep
    cannot tell that documentation from a live default."""
    import ast
    import pathlib
    repo = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in sorted((repo / "scripts").glob("*.py")) + \
            sorted((repo / "kawa").rglob("*.py")):
        if path.name == "nodehealth.py":
            continue                    # the one place the default may live
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {id(ast.get_docstring(n, clean=False))
                      for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef,
                                        ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and "~/.kawa/status" in node.value
                    and id(node.value) not in docstrings):
                offenders.append(f"{path.relative_to(repo)}:{node.lineno}")
    assert offenders == [], (
        f"hardcoded status paths escape KAWA_STATUS_DIR: {offenders}. "
        f"Use os.path.join(nodehealth.status_dir(), ...).")


def test_every_unit_status_name_matches_its_own_unit():  # type: ignore[no-untyped-def]
    """A resident's status filename must derive the SAME component key its
    OnFailure marker does, or the marker can never be superseded.

    Found by round-1 review of #228, not by me: kawa-replica-pull.service
    derives `replica-pull`, its script wrote `replica.status` (`replica`), and
    the two never met — so a replica-pull failure marker would have become the
    permanent furniture this design exists to avoid. Five residents happened to
    agree; one did not, and nothing was checking. This walks unit -> ExecStart
    script -> the `*.status` literal that script writes, so the next resident
    added cannot reintroduce it."""
    import ast
    import pathlib
    import re
    repo = pathlib.Path(__file__).resolve().parent.parent
    checked = []
    for unit in sorted((repo / "ops" / "systemd").glob("kawa-*.service")):
        text = unit.read_text(encoding="utf-8")
        if "@" in unit.name:
            continue                     # the template IS the hook; it has no marker
        # Round 2 of #228: skipping units WITHOUT OnFailure= verified only the
        # ones that had already opted in — so a resident added later without
        # wiring it escaped the check entirely, reopening the exact defect this
        # change exists to close (three collectors, no hook, no trace). Require
        # it. A unit that genuinely should not report failures has to say so
        # here, deliberately, rather than by omission.
        assert "OnFailure=kawa-onfail@%n.service" in text, (
            f"{unit.name} has no OnFailure hook — a failure of this unit would "
            f"leave no marker for kawa/nodehealth.py to read.")
        m = re.search(r"^ExecStart=.*?(scripts/[\w./-]+\.py)", text, re.M)
        assert m, f"{unit.name}: no python ExecStart to trace"
        script = repo / m.group(1)
        names = {n.value[: -len(".status")]
                 for n in ast.walk(ast.parse(script.read_text(encoding="utf-8")))
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and n.value.endswith(".status")}
        assert len(names) == 1, f"{script.name}: expected one status name, got {names}"
        expected = nodehealth._component(unit.name)
        assert names == {expected}, (
            f"{unit.name} derives component {expected!r} but {m.group(1)} writes "
            f"{names.pop()!r}.status — an OnFailure marker for this unit could "
            f"never be superseded by a successful run.")
        checked.append(unit.name)
    assert len(checked) >= 5, f"only traced {checked} — the walk stopped finding units"
