"""Step 12C: the resident pull loop — wake is a hint, the tick is authoritative. (#129 rev 3)

Usage (resident on the dogfood node, Type=notify under systemd):
  KAWA_DSN=dbname=kawa KAWA_NODE=panoplia python scripts/supervisor.py
      [--tick 60] [--once]
      [--status-file ~/.kawa/status/supervisor.status]
      [--state-file ~/.kawa/supervisor.state.json]
      [--credential ~/.kawa/node_credential.json] [--keys ~/.kawa/keys.json]
      [--bridge broker|none] [--broker-url http://127.0.0.1:8000]

Every tick (T = 60s, durability-policy `## Supervisor (12C)`):
  1. WATCHDOG=1 to systemd — supervision is EXTERNAL (#129 R7: `WatchdogSec` +
     `OnFailure=`); the loop never self-certifies its own liveness.
  2. pull the ready Work set from `current_work` — pull is authoritative (§7.1);
     a lost wake hint degrades to this tick, never to ignorance.
  3. Work first seen ready ⇒ surface it:
       - journald line + status file (FIRST-CLASS surface, R5)
       - ONE signed `work_surfaced` Observation per Work: value_number =
         propagation seconds = T_surfaced − T_eligible, where T_eligible is
         `events.recorded_at` of the eligibility-completing event and T_surfaced
         is this tick's status-file timestamp — both from THIS node's clock (F2)
       - a broker bridge message (SECONDARY, deprecated-at-birth per F3; a bridge
         failure lands in `bridge_error` and NEVER fails the tick)
  4. write the status file ALWAYS — success or failure. It carries
     `"bridge": "deprecated-active"` until a `bridge_exit_accepted` Observation
     exists (F3: the inertia is visible on every tick).

At-least-once surfacing: the state file is committed AFTER the Observation and the
status write; kill anywhere and rerun — the worst case is a duplicate surfacing,
never a silent omission. A Work that leaves `ready` is pruned from the state, so
becoming eligible AGAIN surfaces again (that is a new eligibility, not a dupe).

Loud path (R2): any tick exception ⇒ status file ok=false + exit 2; systemd
`Restart=on-failure` revives with a clean connection, `OnFailure=` records the
failure where the operator looks.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import signal
import socket
import sys
import time
import urllib.request

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.storage.db import connect

POLICY_DOC = os.path.join(os.path.dirname(__file__), os.pardir, "docs",
                          "durability-policy-v0.1.md")


def policy_digest() -> str:
    with open(POLICY_DOC, "rb") as f:
        return "sha256:" + hashlib.sha256(f.read()).hexdigest()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sd_notify(msg: str) -> None:
    """Minimal sd_notify(3) — no dependency; silently a no-op outside systemd."""
    target = os.environ.get("NOTIFY_SOCKET")
    if not target:
        return
    addr = "\0" + target[1:] if target.startswith("@") else target
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(msg.encode(), addr)
    except OSError:
        pass  # notification loss must never break the tick (wake is a hint)


def read_ready(conn) -> list[dict]:
    """The ready Work set, with T_eligible per #129 F2: `events.recorded_at` of the
    event that completed eligibility. `current_work.latest_event_id` at the moment
    a Work turns ready IS that event; `ready_at` (reducer clock_timestamp in the
    same transaction) is the documented fallback if the row advanced since."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT w.plan_ref, w.work_ref, w.work_kind, w.role_requirement, "
            "       w.ready_at, e.recorded_at "
            "FROM current_work w LEFT JOIN events e ON e.event_id = w.latest_event_id "
            "WHERE w.execution='ready' ORDER BY w.ready_at NULLS LAST, w.work_ref")
        return [{"plan_ref": r[0], "work_ref": r[1], "work_kind": r[2],
                 "role": r[3], "ready_at": r[4], "eligible_at": r[5] or r[4]}
                for r in cur.fetchall()]


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state.setdefault("surfaced", {})
    return state


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def broker_bridge(broker_url: str, node_ref: str, api_key: str):
    """SECONDARY surface (#129 R5/F3): a broker message to the node's operator
    agent through the existing dispatch path. Deprecated at birth — retires on a
    recorded `bridge_exit_accepted` Observation, not on silence."""
    def send(new_items: list[dict]) -> None:
        lines = [f"[kawa supervisor] {len(new_items)} newly-eligible Work:"]
        lines += [f"  [{w['role'] or '-'}] {w['work_ref']}  ({w['plan_ref']} · {w['work_kind']})"
                  for w in new_items]
        lines.append("Pull it from kawa (scripts/brief.py); record a Result to advance.")
        body = json.dumps({"content": "\n".join(lines), "task_kind": "message",
                           "target_agent_id": f"{node_ref}-cc-primary"}).encode()
        req = urllib.request.Request(
            f"{broker_url}/tasks/dispatch", data=body,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3.0).read()
    return send


def resolve_broker_key(node_ref: str) -> str:
    """env first; else the broker checkout's .env (the bridge is best-effort —
    an empty key just means the bridge reports an error in the status file)."""
    key = os.environ.get("KAWA_BROKER_API_KEY", "")
    if key:
        return key
    env_path = os.path.expanduser("~/memory-broker/.env")
    want = f"API_KEY_CC_{node_ref.upper()}"
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith(want + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def run_tick(conn, *, kawa: Kawa, node_ref: str, state: dict, status_file: str,
             state_file: str, bridge_fn, bridge_label: str, tick_no: int,
             policy_dgst: str, now_fn=_now) -> dict:
    """One tick against an open connection. Factored for the test suite — main()
    wraps it with connect(), the watchdog ping, and the loud exit code."""
    ready = read_ready(conn)
    ready_refs = [w["work_ref"] for w in ready]
    new = [w for w in ready if w["work_ref"] not in state["surfaced"]]

    t_surfaced = now_fn()
    status: dict = {"ts": _iso(t_surfaced), "ok": True, "tick": tick_no,
                    "node": node_ref, "policy_digest": policy_dgst,
                    "ready": ready_refs,
                    "surfaced_this_tick": [], "bridge": bridge_label, "pid": os.getpid()}

    for w in new:
        eligible_at = w["eligible_at"]
        delta = (t_surfaced - eligible_at).total_seconds() if eligible_at else None
        # journald IS the first-class surface (R5) — stdout under systemd
        print(f"work_surfaced {w['work_ref']} plan={w['plan_ref']} role={w['role'] or '-'} "
              f"propagation_s={delta if delta is not None else 'unknown'}", flush=True)
        # sign-at-birth (12B-1): the node credential attests the surfacing.
        # content_digest closes the #98 §2 source-binding tuple: what was read
        # from the projection IS the surfaced tuple, hashed canonically.
        surfaced_tuple = json.dumps(
            {"work_ref": w["work_ref"], "plan_ref": w["plan_ref"],
             "eligible_at": _iso(eligible_at) if eligible_at else None,
             "surfaced_at": _iso(t_surfaced)}, sort_keys=True)
        kawa.record_observation(
            "work_surfaced", value_number=delta, method="metric_read",
            source_ref=f"kawa://current_work/{w['work_ref']}",
            source_revision=(f"tick={tick_no} t_eligible={_iso(eligible_at) if eligible_at else '?'} "
                             f"t_surfaced={_iso(t_surfaced)} policy_digest={policy_dgst}"),
            content_digest="sha256:" + hashlib.sha256(surfaced_tuple.encode()).hexdigest(),
            fetched_at=_iso(t_surfaced))
        conn.commit()
        status["surfaced_this_tick"].append(
            {"work_ref": w["work_ref"], "propagation_s": delta})

    if new and bridge_fn is not None:
        try:
            bridge_fn(new)
        except Exception as exc:  # the bridge degrades reach, never correctness (R5)
            status["bridge_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    os.makedirs(os.path.dirname(status_file) or ".", exist_ok=True)
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)

    # state commits LAST: kill anywhere above ⇒ re-surface (duplicate, not silence)
    state["surfaced"] = {ref: ts for ref, ts in state["surfaced"].items() if ref in ready_refs}
    for w in new:
        state["surfaced"][w["work_ref"]] = status["ts"]
    save_state(state_file, state)
    return status


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tick", type=float, default=60.0)     # policy value (R2)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status-file", default=os.path.expanduser("~/.kawa/status/supervisor.status"))
    ap.add_argument("--state-file", default=os.path.expanduser("~/.kawa/supervisor.state.json"))
    ap.add_argument("--credential", default=os.path.expanduser("~/.kawa/node_credential.json"))
    ap.add_argument("--keys", default=os.path.expanduser("~/.kawa/keys.json"))
    ap.add_argument("--bridge", choices=["broker", "none"], default="broker")
    ap.add_argument("--broker-url", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    node = os.environ.get("KAWA_NODE") or os.uname().nodename.split(".")[0]

    stop = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(flag=True))

    bridge_fn = None
    bridge_label = "none"
    if args.bridge == "broker":
        bridge_fn = broker_bridge(args.broker_url, node, resolve_broker_key(node))
        bridge_label = "deprecated-active"   # F3: visible inertia until bridge_exit_accepted

    pdg = policy_digest()
    tick_no = 0
    try:
        with connect() as conn:
            cred = load_or_create_local_node(args.credential, node_ref=node)
            keys = PublicKeyRegistry(args.keys)
            keys.register(cred.signing_key_ref, cred.public_pem())
            kawa = Kawa(conn, identity=IdentityContext.from_local_node(cred, actor_ref="supervisor"))
            state = load_state(args.state_file)
            sd_notify("READY=1")
            while not stop["flag"]:
                tick_no += 1
                sd_notify("WATCHDOG=1")
                status = run_tick(conn, kawa=kawa, node_ref=node, state=state,
                                  status_file=args.status_file, state_file=args.state_file,
                                  bridge_fn=bridge_fn, bridge_label=bridge_label,
                                  tick_no=tick_no, policy_dgst=pdg)
                if args.once:
                    print(json.dumps(status, indent=2))
                    return 0
                deadline = time.monotonic() + args.tick
                while not stop["flag"] and time.monotonic() < deadline:
                    time.sleep(min(1.0, deadline - time.monotonic()))
    except Exception as exc:
        # loud path (R2): status ok=false + exit 2; systemd restarts and OnFailure records
        try:
            os.makedirs(os.path.dirname(args.status_file) or ".", exist_ok=True)
            with open(args.status_file, "w", encoding="utf-8") as f:
                json.dump({"ts": _iso(_now()), "ok": False, "tick": tick_no,
                           "error_class": type(exc).__name__,
                           "error": str(exc)[:300]}, f, indent=2)
        except OSError:
            pass
        print(f"supervisor FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    sd_notify("STOPPING=1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
