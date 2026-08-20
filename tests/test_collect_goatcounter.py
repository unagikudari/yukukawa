"""GoatCounter collector invariants (review round 1 findings, as literal tests).

Fixture provenance: tests/fixtures/goatcounter_stats_total_2026-08-17.json is a
REAL captured `/api/v0/stats/total` response (2026-08-17, total=3 first-visits)
— fixtures come from real measurements, never invented shapes."""
from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.error

import pytest

psycopg = pytest.importorskip("psycopg")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

import collect_goatcounter as cg  # noqa: E402

_FIXTURE = os.path.join(_REPO, "tests", "fixtures",
                        "goatcounter_stats_total_2026-08-17.json")
_RAW = open(_FIXTURE, "rb").read()
_TOTAL = json.loads(_RAW)["total"]
_SITE = "https://example.goatcounter.com"


@pytest.fixture()
def conn():  # type: ignore[no-untyped-def]
    try:
        c = psycopg.connect(os.environ["KAWA_TEST_DSN_A"], autocommit=False)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"test DB unavailable: {exc}")
    with c.cursor() as cur:
        cur.execute("TRUNCATE content_embedding, event_content, events, event_links, "
                    "event_link, event_observation, event_claim, event_plan, event_work, "
                    "event_work_dependency, event_work_retired, event_result, "
                    "current_claim_standing, current_plans, current_work, "
                    "current_work_dependency, runtime_work_occupancy, work_dispatch")
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def fetched(monkeypatch):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def fake(site, token, day):  # type: ignore[no-untyped-def]
        calls.append(day)
        return _TOTAL, _RAW
    monkeypatch.setattr(cg, "fetch_day_total", fake)
    return calls


def _run(conn, tmp_path, day, **kw):  # type: ignore[no-untyped-def]
    args = dict(site=_SITE, token="t", day=day, partial_ok=False,
                since="2026-08-17", node_ref="test",
                credential_path=str(tmp_path / "cred.json"),
                keys_path=str(tmp_path / "keys.json"))
    args.update(kw)
    return cg.run(conn, **args)


def _yesterday() -> str:
    return (datetime.datetime.now(datetime.timezone.utc).date()
            - datetime.timedelta(days=1)).isoformat()


def _today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def test_finalized_day_records_typed_observation(conn, tmp_path, fetched):  # type: ignore[no-untyped-def]
    day = _yesterday()
    status = _run(conn, tmp_path, day, since=day)
    assert status["total"] == _TOTAL and status["partial"] is False
    with conn.cursor() as cur:
        cur.execute("SELECT value_number, occurred_at, source_revision, "
                    "content_digest, observation_method_class "
                    "FROM event_observation WHERE predicate=%s", (cg.PREDICATE,))
        rows = cur.fetchall()
    assert len(rows) == 1
    num, occurred, rev, digest, method = rows[0]
    assert num == float(_TOTAL)                       # visits, exact
    assert occurred == f"{day}T00:00:00Z"
    assert rev == f"site=example.goatcounter.com day={day} metric=first_visits partial=false"
    assert digest.startswith("sha256:") and method == "api_fetch"


def test_finalized_rerun_dedups(conn, tmp_path, fetched):  # type: ignore[no-untyped-def]
    day = _yesterday()
    _run(conn, tmp_path, day, since=day)
    status = _run(conn, tmp_path, day, since=day)
    assert status["skipped"] == "Observation already recorded"
    assert len(fetched) == 1                          # second run never hits the API


def test_partial_needs_flag_and_is_bounded_to_one(conn, tmp_path, fetched):  # type: ignore[no-untyped-def]
    day = _today()
    assert "not complete" in _run(conn, tmp_path, day)["skipped"]
    first = _run(conn, tmp_path, day, partial_ok=True)
    assert first["partial"] is True
    again = _run(conn, tmp_path, day, partial_ok=True)
    assert again["skipped"] == "Observation already recorded"    # no partial pileup


def test_finalized_supersedes_partial_without_rewrite(conn, tmp_path, fetched, monkeypatch):  # type: ignore[no-untyped-def]
    day = _today()
    _run(conn, tmp_path, day, partial_ok=True)
    # freeze "today" to tomorrow: the same day is now finalized
    monkeypatch.setattr(cg, "_utc_today",
                        lambda: datetime.date.fromisoformat(day) + datetime.timedelta(days=1))
    status = _run(conn, tmp_path, day)
    assert status["partial"] is False                 # records despite the partial
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM event_observation WHERE predicate=%s",
                    (cg.PREDICATE,))
        assert cur.fetchone()[0] == 2                 # both events remain — append-only


def test_pre_instrument_day_is_refused_not_zero(conn, tmp_path, fetched):  # type: ignore[no-untyped-def]
    status = _run(conn, tmp_path, "2026-08-16", since="2026-08-17")
    assert "before instrument coverage" in status["skipped"]
    assert fetched == []                              # never even fetched


def test_future_since_is_config_error(conn, tmp_path, fetched):  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="configuration error"):
        _run(conn, tmp_path, _yesterday(), since="2999-01-01")


def test_day_is_canonicalized_before_use(conn, tmp_path, fetched):  # type: ignore[no-untyped-def]
    day = _yesterday()
    compact = day.replace("-", "")                    # e.g. 20260816 — valid ISO, wrong form
    status = _run(conn, tmp_path, compact, since=day)
    assert status["day"] == day                       # canonical everywhere
    assert fetched == [day]                           # URL built from canonical form
    status2 = _run(conn, tmp_path, day, since=day)
    assert status2["skipped"] == "Observation already recorded"   # dedup sees same key


def test_redirect_is_refused_before_token_travels():  # type: ignore[no-untyped-def]
    h = cg._NoRedirect()
    with pytest.raises(RuntimeError, match="refusing redirect"):
        h.redirect_request(None, None, 302, "Found", {}, "https://evil.example/steal")


def test_main_refuses_non_https_site(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GOATCOUNTER_SITE", "http://example.goatcounter.com")
    monkeypatch.setenv("GOATCOUNTER_API_TOKEN", "t")
    monkeypatch.setenv("GOATCOUNTER_SINCE", "2026-08-17")
    monkeypatch.setattr(sys, "argv", ["collect_goatcounter.py"])
    assert cg.main() == 2                             # rejected before any network/DB use
    assert "must be https://" in capsys.readouterr().err


def test_main_refuses_missing_since(monkeypatch, capsys):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GOATCOUNTER_SITE", "https://example.goatcounter.com")
    monkeypatch.setenv("GOATCOUNTER_API_TOKEN", "t")
    monkeypatch.delenv("GOATCOUNTER_SINCE", raising=False)
    monkeypatch.setattr(sys, "argv", ["collect_goatcounter.py"])
    assert cg.main() == 2                             # fail-closed: no coverage floor, no run
    assert "GOATCOUNTER_SINCE" in capsys.readouterr().err


# --- reconciliation (2026-08-20: a transient 404 dropped 2026-08-19 forever) ---


def _flaky(monkeypatch, fail_on: set[str]):  # type: ignore[no-untyped-def]
    """Fetcher that fails for named days and records every day attempted."""
    calls: list[str] = []

    def fake(site, token, day):  # type: ignore[no-untyped-def]
        calls.append(day)
        if day in fail_on:
            raise urllib.error.HTTPError(f"u/{day}", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        return _TOTAL, _RAW
    monkeypatch.setattr(cg, "fetch_day_total", fake)
    return calls


def _reconcile(conn, tmp_path, *, max_fetches=10, since="2026-08-17", **kw):  # type: ignore[no-untyped-def]
    args = dict(site=_SITE, token="t", partial_ok=False, since=since,
                node_ref="test", max_fetches=max_fetches,
                credential_path=str(tmp_path / "cred.json"),
                keys_path=str(tmp_path / "keys.json"))
    args.update(kw)
    return cg.reconcile(conn, **args)


def _days_back(n: int) -> str:
    return (datetime.datetime.now(datetime.timezone.utc).date()
            - datetime.timedelta(days=n)).isoformat()


def test_a_days_failure_is_repaired_by_the_next_run(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """THE bug: one transient failure used to lose that day permanently."""
    gap = _days_back(2)
    calls = _flaky(monkeypatch, {gap})
    first = _reconcile(conn, tmp_path, since=_days_back(3))
    assert [f["day"] for f in first["failed"]] == [gap]
    assert gap not in [r["day"] for r in first["recorded"]]

    calls.clear()                                  # the API recovers
    _flaky(monkeypatch, set())
    second = _reconcile(conn, tmp_path, since=_days_back(3))
    assert second["failed"] == []
    assert gap in [r["day"] for r in second["recorded"]]   # backfilled, no operator
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM event_observation WHERE occurred_at=%s",
                    (f"{gap}T00:00:00Z",))
        assert cur.fetchone()[0] == 1              # exactly one, not a duplicate


def test_one_bad_day_does_not_suppress_its_neighbours(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Aborting the loop would re-create the bug in a new shape."""
    bad = _days_back(2)
    _flaky(monkeypatch, {bad})
    out = _reconcile(conn, tmp_path, since=_days_back(3))
    recorded = sorted(r["day"] for r in out["recorded"])
    assert recorded == sorted({_days_back(3), _days_back(1)})   # both sides of the gap
    assert out["failed"][0]["error"].startswith("HTTPError")    # named, not swallowed


def test_recorded_days_cost_no_api_call(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Steady state is one query and nothing else — recorded days are excluded
    before any fetch is planned, not skipped after one is attempted."""
    calls = _flaky(monkeypatch, set())
    _reconcile(conn, tmp_path, since=_days_back(3))
    assert len(calls) == 3
    calls.clear()
    out = _reconcile(conn, tmp_path, since=_days_back(3))
    assert calls == []                             # no API call at all
    assert out["recorded"] == [] and out["missing"] == []


def test_never_reaches_below_the_coverage_floor(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Fetching a pre-instrument day would record a fake zero (the SINCE rule)."""
    calls = _flaky(monkeypatch, set())
    floor = _days_back(2)
    out = _reconcile(conn, tmp_path, since=floor)
    assert min(calls) == floor                     # never looked further back
    assert out["range"][0] == floor


def test_an_old_gap_is_still_repaired_long_after_it_happened(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Round-1 review of #228, finding 2: with a trailing window, a gap that is
    not repaired before it ages past the window falls off the back FOREVER —
    and the status file returns to ok:true, actively reporting health over a
    permanent hole. The floor is the coverage start, so there is no back to
    fall off."""
    old_gap = _days_back(40)
    _flaky(monkeypatch, {old_gap})
    first = _reconcile(conn, tmp_path, since=_days_back(41), max_fetches=99)
    assert [f["day"] for f in first["failed"]] == [old_gap]
    assert first["missing"] == [old_gap]           # named, and the run is not ok

    _flaky(monkeypatch, set())                     # 39 days later, API healthy
    second = _reconcile(conn, tmp_path, since=_days_back(41), max_fetches=99)
    assert old_gap in [r["day"] for r in second["recorded"]]
    assert second["missing"] == []


def test_a_backlog_is_bounded_per_run_and_named_not_dropped(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Bounding API calls is legitimate; bounding the lookback is not. A capped
    run must say what it did not reach, or a silent truncation reads as done."""
    calls = _flaky(monkeypatch, set())
    out = _reconcile(conn, tmp_path, since=_days_back(10), max_fetches=3)
    assert len(calls) == 3                          # the cap is real
    assert len(out["deferred"]) == 7                # and the rest are NAMED
    assert out["missing"] == out["deferred"]        # still missing == still owed
    assert sorted(calls) == calls                   # oldest first: the fragile end

    calls.clear()
    out2 = _reconcile(conn, tmp_path, since=_days_back(10), max_fetches=99)
    assert out2["missing"] == [] and len(calls) == 7   # drains, never re-fetches


def test_today_is_excluded_unless_partial_is_allowed(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    calls = _flaky(monkeypatch, set())
    _reconcile(conn, tmp_path, since=_days_back(1))
    assert _today() not in calls                   # an incomplete day is not a day
    calls.clear()
    _reconcile(conn, tmp_path, since=_days_back(1), partial_ok=True)
    assert _today() in calls


def test_zero_fetch_budget_is_a_config_error_not_a_silent_no_op(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    _flaky(monkeypatch, set())
    with pytest.raises(ValueError, match="max-fetches"):
        _reconcile(conn, tmp_path, max_fetches=0)


# --- the status file: the half that reports the failures ----------------------


def test_status_is_written_on_the_failure_path(tmp_path, monkeypatch, capsys):  # type: ignore[no-untyped-def]
    """A status only written when things went well cannot report that they did not."""
    target = tmp_path / "goatcounter.status"
    monkeypatch.setattr(cg, "STATUS_FILE", str(target))
    monkeypatch.setenv("GOATCOUNTER_SITE", _SITE)
    monkeypatch.setenv("GOATCOUNTER_API_TOKEN", "t")
    monkeypatch.setenv("GOATCOUNTER_SINCE", "2026-08-17")
    monkeypatch.setenv("KAWA_NODE", "test")
    monkeypatch.setattr(sys, "argv", ["collect_goatcounter.py"])
    monkeypatch.setattr(cg, "connect", lambda: (_ for _ in ()).throw(RuntimeError("db gone")))
    assert cg.main() == 2
    st = json.loads(target.read_text())
    assert st["ok"] is False
    assert "db gone" in st["error"] and st["node"] == "test"


def test_status_says_not_ok_when_a_day_failed(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """The run must not report ok:true while a day is missing — that is the
    whole delivery route. Exercised through main(), because main() is where
    the ok/not-ok decision is actually made."""
    target = tmp_path / "goatcounter.status"
    bad = _days_back(2)
    _flaky(monkeypatch, {bad})
    monkeypatch.setattr(cg, "STATUS_FILE", str(target))
    monkeypatch.setenv("GOATCOUNTER_SITE", _SITE)
    monkeypatch.setenv("GOATCOUNTER_API_TOKEN", "t")
    monkeypatch.setenv("GOATCOUNTER_SINCE", _days_back(3))
    monkeypatch.setenv("KAWA_NODE", "test")
    monkeypatch.setattr(sys, "argv", ["collect_goatcounter.py",
                                      "--credential", str(tmp_path / "c.json"),
                                      "--keys", str(tmp_path / "k.json")])

    class _Keep:                      # the fixture owns this connection's life
        def __enter__(self): return conn
        def __exit__(self, *a): return False
    monkeypatch.setattr(cg, "connect", _Keep)

    assert cg.main() == 2                                  # loud exit survives
    st = json.loads(target.read_text())
    assert st["ok"] is False                               # <- the delivery bit
    assert [f["day"] for f in st["failed"]] == [bad]       # and WHICH day
    assert st["missing"] == [bad]                          # the hole is named
    assert len(st["recorded"]) == 2                        # the others still landed


def _main_run(conn, tmp_path, monkeypatch, target, *, since, argv_extra=()):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(cg, "STATUS_FILE", str(target))
    monkeypatch.setenv("GOATCOUNTER_SITE", _SITE)
    monkeypatch.setenv("GOATCOUNTER_API_TOKEN", "t")
    monkeypatch.setenv("GOATCOUNTER_SINCE", since)
    monkeypatch.setenv("KAWA_NODE", "test")
    monkeypatch.setattr(sys, "argv", ["collect_goatcounter.py", *argv_extra,
                                      "--credential", str(tmp_path / "c.json"),
                                      "--keys", str(tmp_path / "k.json")])

    class _Keep:                      # the fixture owns this connection's life
        def __enter__(self): return conn
        def __exit__(self, *a): return False
    monkeypatch.setattr(cg, "connect", _Keep)
    return cg.main()


def test_a_draining_backlog_is_not_an_alarm(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Round 2 of #228: the earlier rule (not-ok whenever the series has a
    hole) fired OnFailure on ~30 consecutive runs of a first backfill that was
    draining exactly as designed. Nothing here fails; the cap simply defers."""
    target = tmp_path / "goatcounter.status"
    _flaky(monkeypatch, set())                     # zero errors
    assert _main_run(conn, tmp_path, monkeypatch, target,
                     since=_days_back(6), argv_extra=("--max-fetches", "2")) == 0
    st = json.loads(target.read_text())
    assert st["ok"] is True                        # quiet: this is normal work
    assert st["failed"] == []
    assert len(st["deferred"]) == 4                # ...but the backlog is VISIBLE
    assert st["missing"] == st["deferred"]


def test_a_stuck_day_stays_loud_on_every_run(conn, tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """The other half of the same rule: silencing the backlog must not silence
    a day that cannot be collected. A stuck day is re-attempted every run and
    lands in `failed` every run, which is what keeps the loud path honest."""
    target = tmp_path / "goatcounter.status"
    bad = _days_back(3)
    for _ in range(2):                             # two consecutive runs
        _flaky(monkeypatch, {bad})
        assert _main_run(conn, tmp_path, monkeypatch, target,
                         since=_days_back(3)) == 2
        st = json.loads(target.read_text())
        assert st["ok"] is False
        assert [f["day"] for f in st["failed"]] == [bad]


def test_status_records_which_days_failed(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    target = tmp_path / "goatcounter.status"
    cg.write_status({"failed": [{"day": "2026-08-19", "error": "HTTPError: 404"}]},
                    node="test", ok=False, path=str(target))
    st = json.loads(target.read_text())
    assert st["ok"] is False and st["failed"][0]["day"] == "2026-08-19"


def test_a_write_that_dies_midway_leaves_the_previous_status_intact(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Round-1 mechanical review of #228: asserting merely that no .tmp file
    remains is vacuous — a non-atomic implementation that writes straight to
    the target passes it too. This asserts the property that matters: the
    reader either sees the OLD status or the new one, never a torn one, and no
    debris is left to be mistaken for either."""
    target = tmp_path / "goatcounter.status"
    cg.write_status({"good": True}, node="test", ok=True, path=str(target))
    before = target.read_text()

    def explode(*a, **kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("disk full mid-serialise")
    monkeypatch.setattr(cg.json, "dump", explode)
    with pytest.raises(RuntimeError):
        cg.write_status({"ok": False}, node="test", ok=False, path=str(target))

    assert target.read_text() == before                      # untorn, unchanged
    assert not (tmp_path / "goatcounter.status.tmp").exists()  # and no debris


def test_a_bare_filename_is_writable(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """os.path.dirname("x.status") is "", and os.makedirs("") raises."""
    monkeypatch.chdir(tmp_path)
    cg.write_status({"ok": True}, node="test", ok=True, path="goatcounter.status")
    assert json.loads((tmp_path / "goatcounter.status").read_text())["ok"] is True
