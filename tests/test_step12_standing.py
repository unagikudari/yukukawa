"""step-12 standing checker (scripts/step12_standing.py) — the machine gate behind
w-step12-durability-dogfood's Result. The streak/assembly logic is pure and tested
exhaustively; compute() is tested against a live test-DB log built through the normal
Kawa API. Fixture values mirror the REAL measured shapes from the panoplia dogfood log
(archive_cycle source_revision format, the 56.349s propagation measurement) — a fixture
invented from imagination fixes the author's misunderstanding in green."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os

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
UTC = dt.timezone.utc


def test_streak_counts_consecutive_ok_days_backwards_from_latest() -> None:
    rows = [(D(2026, 8, d), True, True, DIG) for d in range(10, 17)]
    streak, gaps = standing.archive_streak(rows, as_of_date=D(2026, 8, 16))
    assert streak == 7 and gaps == []


def test_streak_breaks_on_a_missed_day() -> None:
    rows = [(D(2026, 8, 10), True, True, DIG), (D(2026, 8, 12), True, True, DIG),
            (D(2026, 8, 13), True, True, DIG)]
    streak, _ = standing.archive_streak(rows, as_of_date=D(2026, 8, 13))
    assert streak == 2                                   # 13 and 12; the 11th is missing


def test_streak_breaks_loudly_on_a_failed_proof() -> None:
    rows = [(D(2026, 8, 11), True, True, DIG), (D(2026, 8, 12), False, True, DIG),
            (D(2026, 8, 13), True, True, DIG)]
    streak, gaps = standing.archive_streak(rows, as_of_date=D(2026, 8, 13))
    assert streak == 1 and any("FAILED" in g for g in gaps)


def test_streak_rejects_unsigned_proofs() -> None:
    rows = [(D(2026, 8, 12), True, False, DIG), (D(2026, 8, 13), True, True, DIG)]
    streak, gaps = standing.archive_streak(rows, as_of_date=D(2026, 8, 13))
    assert streak == 1 and any("unsigned" in g for g in gaps)


def test_streak_rejects_missing_policy_digest() -> None:
    """A None digest must break the streak, not silently equal another None
    (review b5d0a8bc F4)."""
    rows = [(D(2026, 8, 12), True, True, None), (D(2026, 8, 13), True, True, None)]
    streak, gaps = standing.archive_streak(rows, as_of_date=D(2026, 8, 13))
    assert streak == 0 and any("no policy digest" in g for g in gaps)


def test_streak_restarts_on_policy_digest_change() -> None:
    """A digest change splits the measurement lineage (durability-policy header) — days
    under the OLD policy must not count toward a streak claimed under the new one."""
    old = "sha256:" + "b" * 64
    rows = [(D(2026, 8, 11), True, True, old), (D(2026, 8, 12), True, True, DIG),
            (D(2026, 8, 13), True, True, DIG)]
    streak, gaps = standing.archive_streak(rows, as_of_date=D(2026, 8, 13))
    assert streak == 2 and any("lineage split" in g for g in gaps)


def test_streak_same_day_rerun_latest_wins() -> None:
    """Kill-anywhere rerun-safety means a day can hold several runs; the day counts once
    and the latest run's row decides it."""
    rows = [(D(2026, 8, 13), False, True, DIG), (D(2026, 8, 13), True, True, DIG)]
    streak, gaps = standing.archive_streak(rows, as_of_date=D(2026, 8, 13))
    assert streak == 1 and gaps == []


def test_streak_dead_loop_is_anchored_to_today() -> None:
    """Seven perfect days that ENDED two weeks ago are a dead loop, not standing
    (review b5d0a8bc F1 — the recency anchor)."""
    rows = [(D(2026, 8, d), True, True, DIG) for d in range(1, 8)]
    streak, gaps = standing.archive_streak(rows, as_of_date=D(2026, 8, 21))
    assert any("not running" in g for g in gaps)
    assert streak == 7                                   # the count stays honest; the gap gates


def test_empty_log_is_zero_standing() -> None:
    streak, gaps = standing.archive_streak([], as_of_date=D(2026, 8, 13))
    assert streak == 0 and gaps


def _green_inputs(now: dt.datetime):
    """All-gates-green inputs shaped like the real dogfood log: daily proofs at the real
    timer hour (15:06Z), the real 56.349s propagation measurement plus the first-run
    backlog artifact (22679s) that must NOT break the gate."""
    proofs = [(now - dt.timedelta(days=n), True, True, DIG)
              for n in range(standing.REQUIRED_DAYS + 1, -1, -1)]
    surfaced = [(now - dt.timedelta(days=8), 22679.540085),
                (now - dt.timedelta(days=8, minutes=-73), 56.349414)]
    return proofs, surfaced


def test_assemble_all_gates_green_is_standing() -> None:
    """The positive path (review b5d0a8bc F5): 8 consecutive daily proofs through today,
    R1 clock >= 7 days, one propagation measurement within bound, drill-2 finished,
    supervisor fresh -> standing True, zero gaps."""
    now = dt.datetime.now(UTC)
    proofs, surfaced = _green_inputs(now)
    report = standing.assemble(proofs, surfaced, "finished", (True, None), now)
    assert report["gaps"] == []
    assert report["standing"] is True
    assert report["archive_streak_days"] >= standing.REQUIRED_DAYS
    assert report["days_elapsed"] >= standing.REQUIRED_DAYS


def test_assemble_each_gate_alone_blocks_standing() -> None:
    """Flip each gate off one at a time from the green baseline — every gate must be
    load-bearing on its own (no false standing when exactly one is red)."""
    now = dt.datetime.now(UTC)
    proofs, surfaced = _green_inputs(now)
    green = (proofs, surfaced, "finished", (True, None))

    r = standing.assemble([], surfaced, "finished", (True, None), now)
    assert not r["standing"]                            # no archive proofs
    r = standing.assemble(proofs, [], "finished", (True, None), now)
    assert not r["standing"]                            # clock never started
    r = standing.assemble(proofs, [(now, 3000.0)], "finished", (True, None), now)
    assert not r["standing"]                            # no measurement within bound
    r = standing.assemble(proofs, surfaced, "ready", (True, None), now)
    assert not r["standing"]                            # drill-2 not finished
    r = standing.assemble(proofs, surfaced, "finished", (False, "supervisor status stale"), now)
    assert not r["standing"]                            # supervisor stale
    assert standing.assemble(*green, now)["standing"]   # and the baseline really is green


def test_compute_reports_gaps_on_a_fresh_log(conn, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """On an empty log every gate reports NOT YET — compute() must name each gap rather
    than fail or claim standing."""
    status = tmp_path / "supervisor.status"
    status.write_text(json.dumps({
        "ts": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": True}))
    report = standing.compute(conn, status_file=str(status))
    assert report["standing"] is False
    assert any("clock has not started" in g for g in report["gaps"])
    assert any("streak 0/7" in g for g in report["gaps"])
    assert report["supervisor_fresh"] is True            # the one green gate


def test_compute_stale_supervisor_is_a_gap(conn, tmp_path) -> None:  # type: ignore[no-untyped-def]
    status = tmp_path / "supervisor.status"
    old = (dt.datetime.now(UTC) - dt.timedelta(seconds=600))
    status.write_text(json.dumps({"ts": old.strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": True}))
    report = standing.compute(conn, status_file=str(status))
    assert report["supervisor_fresh"] is False
    assert any("stale" in g for g in report["gaps"])
