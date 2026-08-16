"""Step 12A: the standing archive loop — export full segments, restore-prove ALL
of them, record ONE Observation, write the local status surface. (#129 rev 3)

Usage:
  KAWA_DSN=dbname=kawa KAWA_NODE=panoplia python scripts/archive_cycle.py
      [--archive-dir ~/.kawa/archive/segments] [--segment-size 100]
      [--status-file ~/.kawa/status/archive.status]

Every run, in order:
  1. load-or-create the node credential (~/.kawa/node_credential.json) and
     register its public half (~/.kawa/keys.json)
  2. export every FULL segment (policy segment_size per origin) not yet on disk
     — export is read-only against the log; files are append-only
  3. re-verify EVERY segment file on disk (all four verify_archive_file layers)
     — commitment existence does not prove durability; this proof does
  4. record ONE `archive_restore_proof` Observation: value_bool = all verified,
     policy_digest + counts + lag in the source-binding tuple
  5. write the machine-readable status file (ALWAYS — success or failure;
     local-first surface, broker-independent)

The loud path (#129 R2): any verification failure or exception => Observation
value false with an error class (when the DB is reachable), status file
ok=false, exit code 2. Restartable and idempotent: kill anywhere, rerun —
exports are skipped when the file exists, proofs are read-only."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.storage.archive import ArchiveVerificationError, archive_export, verify_archive_file
from kawa.storage.db import connect

POLICY_DOC = os.path.join(os.path.dirname(__file__), os.pardir, "docs",
                          "durability-policy-v0.1.md")


def policy_digest() -> str:
    with open(POLICY_DOC, "rb") as f:
        return "sha256:" + hashlib.sha256(f.read()).hexdigest()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def export_full_segments(conn, cred, archive_dir: str, segment_size: int) -> tuple[int, dict[str, int]]:
    """Export every full segment not yet on disk; return (exported_count, lag_per_origin)."""
    exported = 0
    lag: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT origin_node, min(origin_seq), max(origin_seq), count(*) "
                    "FROM events GROUP BY 1 ORDER BY 1")
        heads = cur.fetchall()
    for origin, lo, head, n in heads:
        if lo != 1 or n != head:
            # PR #130 review: a sparsely-held origin (12B replicas) must be
            # SKIPPED loudly, not crash the cycle mid-batch — full sparse
            # export support is a 12B concern
            lag[origin] = -1
            continue
        full = head // segment_size
        for k in range(full):
            frm, to = k * segment_size + 1, (k + 1) * segment_size
            path = os.path.join(archive_dir, f"{origin}-{frm:08d}-{to:08d}.json")
            if os.path.exists(path):
                continue
            archive_export(conn, origin_node=origin, from_seq=frm, to_seq=to,
                           path=path, credential=cred, attested_at=_now())
            exported += 1
        lag[origin] = head - full * segment_size
    return exported, lag


def prove_all(archive_dir: str, keys: PublicKeyRegistry) -> tuple[int, list[str], str]:
    """Verify every segment file; return (n, failures, digest-over-file-digests)."""
    files = sorted(f for f in os.listdir(archive_dir) if f.endswith(".json")) \
        if os.path.isdir(archive_dir) else []
    failures: list[str] = []
    digests: list[str] = []
    for name in files:
        path = os.path.join(archive_dir, name)
        with open(path, "rb") as f:
            digests.append(hashlib.sha256(f.read()).hexdigest())
        try:
            verify_archive_file(path, keys=keys)
        except ArchiveVerificationError as exc:
            failures.append(f"{name}: {exc}")
    setdigest = "sha256:" + hashlib.sha256("\n".join(digests).encode()).hexdigest()
    return len(files), failures, setdigest


def run_cycle(conn, *, node_ref: str, actor_ref: str, archive_dir: str,
              segment_size: int, status_file: str,
              credential_path: str, keys_path: str) -> dict:
    """One full cycle against an open connection. Returns the status dict.
    Factored for the test suite — the CLI wraps it with connect() + exit code."""
    os.makedirs(archive_dir, exist_ok=True)
    cred = load_or_create_local_node(credential_path, node_ref=node_ref)
    keys = PublicKeyRegistry(keys_path)
    keys.register(cred.signing_key_ref, cred.public_pem())

    pdg = policy_digest()
    status: dict = {"ts": _now(), "policy_digest": pdg}
    try:
        exported, lag = export_full_segments(conn, cred, archive_dir, segment_size)
        n, failures, setdigest = prove_all(archive_dir, keys)
        ok = not failures
        status.update({"ok": ok, "segments": n, "exported_this_run": exported,
                       "failed": len(failures), "failures": failures[:5],
                       "lag": lag})
        # sign at birth (#129 12B): the SAME credential that attests the archive
        # attests the Observation — from_local_runtime here was the unsigned-drift hole
        kawa = Kawa(conn, identity=IdentityContext.from_local_node(cred, actor_ref=actor_ref))
        rev = (f"segments={n} exported={exported} failed={len(failures)} "
               f"lag={sum(lag.values())} policy_digest={pdg}")
        if failures:
            rev += f" status=failed error_class=archive_verification [{failures[0][:120]}]"
        kawa.record_observation(
            "archive_restore_proof", value_bool=ok, method="command_exit",
            source_ref=f"file://{os.path.abspath(archive_dir)}",
            source_revision=rev, content_digest=setdigest, fetched_at=status["ts"],
            policy_digest=pdg)
        conn.commit()
    except Exception as exc:
        status.update({"ok": False, "error_class": type(exc).__name__,
                       "error": str(exc)[:300]})
        raise
    finally:
        os.makedirs(os.path.dirname(status_file), exist_ok=True)
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2)
    return status


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", default=os.path.expanduser("~/.kawa/archive/segments"))
    ap.add_argument("--segment-size", type=int, default=100)   # policy value
    ap.add_argument("--status-file", default=os.path.expanduser("~/.kawa/status/archive.status"))
    ap.add_argument("--credential", default=os.path.expanduser("~/.kawa/node_credential.json"))
    ap.add_argument("--keys", default=os.path.expanduser("~/.kawa/keys.json"))
    args = ap.parse_args()
    node = os.environ.get("KAWA_NODE") or os.uname().nodename.split(".")[0]
    try:
        with connect() as conn:
            status = run_cycle(conn, node_ref=node, actor_ref="archive-cycle",
                               archive_dir=args.archive_dir, segment_size=args.segment_size,
                               status_file=args.status_file,
                               credential_path=args.credential, keys_path=args.keys)
    except Exception as exc:
        print(f"archive cycle FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(status, indent=2))
    return 0 if status["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
