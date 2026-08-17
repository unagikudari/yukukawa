"""GoatCounter collector invariants (review round 1 findings, as literal tests).

Fixture provenance: tests/fixtures/goatcounter_stats_total_2026-08-17.json is a
REAL captured `/api/v0/stats/total` response (2026-08-17, total=3 first-visits)
— fixtures come from real measurements, never invented shapes."""
from __future__ import annotations

import datetime
import json
import os
import sys

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
