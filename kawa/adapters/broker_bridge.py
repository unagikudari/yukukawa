"""Kawa → runtime dispatch bridge (#56 §12 compatibility adapter).

Kawa owns the truth — the Work (derived identity) and its Result (a durable, typed Event). The
legacy broker is used only as a replaceable *wake transport* (#53 §17). The request context and
the FULL Result body live in Kawa, so nothing is lost to a transport note's truncation — the
concrete #56/#59 motivation, proven by using this for real reviews.

Flow:
    request_review()  -> record the request in Kawa (work_dispatch: pending)
    [transport]       -> the caller hands `context` to an eligible runtime via the broker
    mark_dispatched() -> record the transport's coordination hint (broker_task_id)
    complete_review() -> record the runtime's full output as a Kawa Result Event (durable)

work_dispatch is coordination, not Domain truth and not a projection: the dispatch adapter owns
it (reducers do not), and it carries no Authority (a dispatch is not a claim of standing, #53 §11).
"""
from __future__ import annotations

import psycopg

from kawa.application.services import Kawa


def request_review(conn: psycopg.Connection, *, work_ref: str, context: str,
                   target_agent: str, transport: str = "broker") -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO work_dispatch (work_ref, context, target_agent, transport, dispatch_state) "
            "VALUES (%s,%s,%s,%s,'pending') "
            "ON CONFLICT (work_ref) DO UPDATE SET context=EXCLUDED.context, "
            "target_agent=EXCLUDED.target_agent, transport=EXCLUDED.transport, "
            "dispatch_state='pending', updated_at=clock_timestamp()",
            (work_ref, context, target_agent, transport),
        )
    conn.commit()


def pending_dispatches(conn: psycopg.Connection) -> list[tuple[str, str, str | None]]:
    """READY review Work with a pending dispatch = what the transport should carry now.

    Note: readiness lives in the projection, so a missed wake never loses a dispatch (#53 §17)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.work_ref, d.context, d.target_agent FROM work_dispatch d "
            "JOIN current_work w ON w.work_ref = d.work_ref "
            "WHERE d.dispatch_state = 'pending' AND w.execution = 'ready' "
            "ORDER BY d.created_at",
        )
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def mark_dispatched(conn: psycopg.Connection, work_ref: str, broker_task_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE work_dispatch SET dispatch_state='dispatched', broker_task_id=%s, "
            "updated_at=clock_timestamp() WHERE work_ref=%s",
            (broker_task_id, work_ref),
        )
    conn.commit()


def complete_review(kawa: Kawa, conn: psycopg.Connection, *, work_ref: str, outcome: str,
                    result_ref: str, body: str) -> None:
    """The durable win: the runtime's full output becomes a Kawa Result Event — typed and
    complete, not a truncatable transport note. This also drives result-driven readiness for any
    downstream Work (the reducer unblocks dependents)."""
    kawa.record_result(work_ref, outcome, result_ref, summary=body)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE work_dispatch SET dispatch_state='completed', updated_at=clock_timestamp() "
            "WHERE work_ref=%s",
            (work_ref,),
        )
    conn.commit()


def dispatch_status(conn: psycopg.Connection, work_ref: str) -> tuple[str, str | None] | None:
    with conn.cursor() as cur:
        cur.execute("SELECT dispatch_state, broker_task_id FROM work_dispatch WHERE work_ref=%s", (work_ref,))
        row = cur.fetchone()
    return (row[0], row[1]) if row else None
