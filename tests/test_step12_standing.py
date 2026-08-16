"""step-12 standing checker (scripts/step12_standing.py) — the machine gate behind
w-step12-durability-dogfood's Result. The streak logic is pure and tested exhaustively;
compute() is tested against a live test-DB log built through the normal Kawa API."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys

import psycopg
import pytest


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


_SPEC = importlib.util.spec_from_file_location(
    "step12_standing",
    os.path.join(os.path.dirname(__file__), os.pardir, "scripts", "step12_standing.py"))
standing = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(standing)

D = dt.date
DIG = "sha256:" + "a" * 64


def test_streak_counts_consecutive_ok_days_backwards_from_latest() -> None:
    rows = [(D(2026, 8, d), True, True, DIG) for d in range(10, 17)]
    streak, gaps = standing.archive_streak(rows)
    assert streak == 7 and gaps == []


def test_streak_breaks_on_a_missed_day() -> None:
    rows = [(D(2026, 8, 10), True, True, DIG), (D(2026, 8, 12), True, True, DIG),
            (D(2026, 8, 13), True, True, DIG)]
    streak, _ = standing.archive_streak(rows)
    assert streak == 2                                   # 13 and 12; the 11th is missing


def test_streak_breaks_loudly_on_a_failed_proof() -> None:
    rows = [(D(2026, 8, 11), True, True, DIG), (D(2026, 8, 12), False, True, DIG),
            (D(2026, 8, 13), True, True, DIG)]
    streak, gaps = standing.archive_streak(rows)
    assert streak == 1 and any("FAILED" in g for g in gaps)


def test_streak_rejects_unsigned_proofs() -> None:
    rows = [(D(2026, 8, 12), True, False, DIG), (D(2026, 8, 13), True, True, DIG)]
    streak, gaps = standing.archive_streak(rows)
    assert streak == 1 and any("unsigned" in g for g in gaps)


def test_streak_restarts_on_policy_digest_change() -> None:
    """A digest change splits the measurement lineage (durability-policy header) — days
    under the OLD policy must not count toward a streak claimed under the new one."""
    old = "sha256:" + "b" * 64
    rows = [(D(2026, 8, 11), True, True, old), (D(2026, 8, 12), True, True, DIG),
            (D(2026, 8, 13), True, True, DIG)]
    streak, gaps = standing.archive_streak(rows)
    assert streak == 2 and any("lineage split" in g for g in gaps)


def test_streak_same_day_rerun_latest_wins() -> None:
    """Kill-anywhere rerun-safety means a day can hold several runs; the day counts once
    and the latest run's row decides it."""
    rows = [(D(2026, 8, 13), False, True, DIG), (D(2026, 8, 13), True, True, DIG)]
    streak, gaps = standing.archive_streak(rows)
    assert streak == 1 and gaps == []


def test_empty_log_is_zero_standing() -> None:
    streak, gaps = standing.archive_streak([])
    assert streak == 0 and gaps


def test_compute_reports_gaps_on_a_fresh_log(conn, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """On an empty log every gate reports NOT YET — compute() must name each gap rather
    than fail or claim standing."""
    status = tmp_path / "supervisor.status"
    status.write_text(json.dumps({
        "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": True}))
    report = standing.compute(conn, status_file=str(status))
    assert report["standing"] is False
    assert any("clock has not started" in g for g in report["gaps"])
    assert any("streak 0/7" in g for g in report["gaps"])
    assert report["supervisor_fresh"] is True            # the one green gate


def test_compute_stale_supervisor_is_a_gap(conn, tmp_path) -> None:  # type: ignore[no-untyped-def]
    status = tmp_path / "supervisor.status"
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=600))
    status.write_text(json.dumps({"ts": old.strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": True}))
    report = standing.compute(conn, status_file=str(status))
    assert report["supervisor_fresh"] is False
    assert any("stale" in g for g in report["gaps"])
