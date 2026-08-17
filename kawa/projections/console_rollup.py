"""Console phase-1 refresh reducers: fleet_node + situation_rollup (+ their
projection_state rows). Step 2 of plan-console-phase1 (#181 rev 3).

These are REFRESH reducers, not per-event reducers: both tables are derived
aggregates over the event log and the other projections, recomputed
deterministically on demand (DELETE + INSERT in one transaction). They write
ONLY what the step-1 contract (sql/0021) admits:

  * reachability / replication / workload: no admissible source exists today
    (no reachability event kind; no registered replica; work_dispatch carries
    an agent STRING, not a typed node ref — parsing a prefix would be an
    invented mapping) => every cell is written UNKNOWN, and the schema's
    CHECKs would reject anything else without a named source anyway.
  * AUTHORITY / PROJECTION_FRESHNESS cards: INCOMPLETE with their named gap.
  * counts always travel with their non-green residue.

The caller owns the transaction (commit/rollback), matching reducers.reduce.
"""
from __future__ import annotations

import psycopg

_DIMENSIONS = ("AUTHORITY", "EXECUTION", "EPISTEMIC", "HEALTH_REACH",
               "PROJECTION_FRESHNESS")
_EXEC_NONGREEN = ("blocked", "retryable", "execution_unknown")
_STANDING_NONGREEN = ("contradicted", "contested")
_AUTHORITY_GAP = "verifier read-model absent (phase 2+)"
_FRESHNESS_GAP = "core projections do not report state yet (only fleet_node, situation_rollup register)"


def _residue(refs: list[str], limit: int = 10) -> str | None:
    if not refs:
        return None
    listed = ", ".join(refs[:limit])
    return listed if len(refs) <= limit else f"{listed} (+{len(refs) - limit} more)"


def refresh_fleet_node(cur: psycopg.Cursor) -> None:
    """One row per origin node seen in the log. Every health cell is UNKNOWN
    until a real, named source exists (measured 2026-08-17: none does)."""
    cur.execute("DELETE FROM fleet_node")
    cur.execute(
        "INSERT INTO fleet_node (node_ref, last_origin_seq, last_event_at) "
        "SELECT origin_node, max(origin_seq), max(recorded_at) "
        "FROM events GROUP BY origin_node")
    # reachability / workload / replication stay at their UNKNOWN defaults —
    # deliberately no UPDATE here; writing a measured state requires a named
    # source (schema CHECK) and no admissible source exists yet.


def refresh_situation_rollup(cur: psycopg.Cursor) -> None:
    """Exactly five dimension rows, each derived from its own projection —
    never merged, counts with residue, INCOMPLETE gaps named."""
    cur.execute("DELETE FROM situation_rollup")

    # AUTHORITY — INCOMPLETE-by-design (#181 rev 2)
    cur.execute(
        "INSERT INTO situation_rollup (dimension, state, gap, source_projection, as_of) "
        "VALUES ('AUTHORITY','INCOMPLETE',%s,'none (deferred)',clock_timestamp())",
        (_AUTHORITY_GAP,))

    # EXECUTION — current_work / work_dispatch
    cur.execute("SELECT work_ref, execution FROM current_work")
    rows = cur.fetchall()
    nongreen = [r[0] for r in rows if r[1] in _EXEC_NONGREEN]
    cur.execute(
        "INSERT INTO situation_rollup (dimension, state, count_total, count_nongreen, "
        "residue, source_projection, as_of) "
        "VALUES ('EXECUTION',%s,%s,%s,%s,'current_work',"
        "coalesce((SELECT max(updated_at) FROM current_work), clock_timestamp()))",
        ("ATTENTION" if nongreen else "OK", len(rows), len(nongreen), _residue(nongreen)))

    # EPISTEMIC — current_claim_standing
    cur.execute("SELECT claim_event_id, standing FROM current_claim_standing")
    rows = cur.fetchall()
    nongreen = [r[0][:24] for r in rows if r[1] in _STANDING_NONGREEN]
    cur.execute(
        "INSERT INTO situation_rollup (dimension, state, count_total, count_nongreen, "
        "residue, source_projection, as_of) "
        "VALUES ('EPISTEMIC',%s,%s,%s,%s,'current_claim_standing',"
        "coalesce((SELECT max(updated_at) FROM current_claim_standing), clock_timestamp()))",
        ("ATTENTION" if nongreen else "OK", len(rows), len(nongreen), _residue(nongreen)))

    # HEALTH_REACH — roll-up of fleet_node (refresh_fleet_node ran first)
    cur.execute("SELECT node_ref, reachability, workload, replication FROM fleet_node")
    rows = cur.fetchall()
    bad = [r[0] for r in rows
           if r[1] == "CRIT" or r[2] in ("ATTENTION", "CRIT") or r[3] == "LAGGING"]
    measured = any(cell != "UNKNOWN" for r in rows for cell in r[1:])
    if bad:
        state = "CRIT" if any(r[1] == "CRIT" for r in rows if r[0] in bad) else "ATTENTION"
    elif not rows or not measured:
        state = "UNKNOWN"          # nodes exist but nothing measured: absence, no gap
    else:
        state = "OK"
    cur.execute(
        "INSERT INTO situation_rollup (dimension, state, count_total, count_nongreen, "
        "residue, source_projection, as_of) "
        "VALUES ('HEALTH_REACH',%s,%s,%s,%s,'fleet_node',"
        "coalesce((SELECT max(updated_at) FROM fleet_node), clock_timestamp()))",
        (state, len(rows), len(bad), _residue(bad)))

    # PROJECTION_FRESHNESS — projection_state (INCOMPLETE: known named gap
    # while the core projections do not register themselves)
    cur.execute("SELECT projection_name, state FROM projection_state")
    rows = cur.fetchall()
    nongreen = [r[0] for r in rows if r[1] != "current"]
    cur.execute(
        "INSERT INTO situation_rollup (dimension, state, gap, count_total, count_nongreen, "
        "residue, source_projection, as_of) "
        "VALUES ('PROJECTION_FRESHNESS','INCOMPLETE',%s,%s,%s,%s,'projection_state',"
        "coalesce((SELECT max(updated_at) FROM projection_state), clock_timestamp()))",
        (_FRESHNESS_GAP, len(rows), len(nongreen), _residue(nongreen)))


def _register_projection_state(cur: psycopg.Cursor) -> None:
    for name in ("fleet_node", "situation_rollup"):
        cur.execute(
            "INSERT INTO projection_state (projection_name, schema_version, last_event_id, "
            "last_recorded_at, state) "
            "SELECT %s, 1, e.event_id, e.recorded_at, 'current' "
            "FROM (SELECT event_id, recorded_at FROM events "
            "      ORDER BY recorded_at DESC, event_id DESC LIMIT 1) e "
            "ON CONFLICT (projection_name) DO UPDATE SET "
            "last_event_id=EXCLUDED.last_event_id, "
            "last_recorded_at=EXCLUDED.last_recorded_at, "
            "state='current', updated_at=clock_timestamp()", (name,))
        # empty log: still register, cursor NULL (honest: nothing consumed yet)
        cur.execute(
            "INSERT INTO projection_state (projection_name, schema_version, state) "
            "VALUES (%s, 1, 'current') ON CONFLICT (projection_name) DO NOTHING", (name,))


def refresh_console_projections(conn: psycopg.Connection) -> None:
    """Refresh both console projections + their projection_state rows.
    Caller commits (or rolls back) — same discipline as reducers.reduce."""
    if conn.autocommit:
        # autocommit would expose the DELETEd-empty state to concurrent readers
        # between statements (step-2 review (b)) — refuse rather than flicker
        raise ValueError("refresh_console_projections requires a transaction-owning "
                         "connection (autocommit=False)")
    with conn.cursor() as cur:
        refresh_fleet_node(cur)
        _register_projection_state(cur)     # before the rollup, so the
        refresh_situation_rollup(cur)       # FRESHNESS card counts this refresh
