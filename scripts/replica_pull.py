"""Step 12B: the standing replica pull loop — one anti-entropy pull per run,
a frontier-lag Observation every cycle, rejects LOUD. (#129 rev 3 F6)

Usage (timer-driven on the replica node; both targets NAMED, fail-closed):
  KAWA_DSN='dbname=kawa port=5434' \
  KAWA_SOURCE_DSN='host=<tailscale-ipv6> dbname=kawa user=kawa_replica password=...' \
  KAWA_NODE=evo python scripts/replica_pull.py
      [--keys ~/.kawa/keys.json] [--trust ~/.kawa/trust.json]
      [--credential ~/.kawa/node_credential.json]
      [--status-file ~/.kawa/status/replica-pull.status] [--scopes fleet]
      [--source-trust PATH --puller-node NODE]

Every run, in order:
  1. read the source frontier (SELECT-only; the source role is read-only)
  2. one `pull()` through the FULL admission gate — keys/trust are THIS node's
     registries; enrollment/seals are operator acts, never automatic
  3. record ONE `replication_frontier_lag` Observation in the LOCAL log (signed —
     the replica's own history is attested at birth)
  4. any admission reject => one `replication_admission_reject` Observation naming
     the reasons + exit 2 (F6: a reject is loud, never a green replica)
  5. write the status file ALWAYS (success or failure; materialized and stub
     counts split per finding 4 — a stub is visible, not a silent gap)

Scoped payloads: the service emits envelope-v2 `fleet`-scoped BY DEFAULT (#113
BC-iii), so without a serving context nearly everything after step 10 would cross
as a stub — a replica of stubs is not a second copy. `--source-trust` (env
`KAWA_SOURCE_TRUST`) names an operator-managed MIRROR of the source's serving
registry; `--puller-node` defaults to this node. The mirror is declared for what
it is: a client-driven pull cannot ENFORCE the offer side (the read-only SQL role
already exposes the rows) — the mirror makes the offer/retain algebra compute the
same sets a serving transport would, until one exists (DEFERRED). Without it,
stubs remain the least-visible fail-direction, visible in the status file.
Restartable and idempotent: `pull()` re-delivery is a no-op; kill anywhere and rerun.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

import psycopg

from kawa import nodehealth
from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry, load_or_create_local_node
from kawa.domain.identity import IdentityContext
from kawa.domain.ids import HLC
from kawa.domain.trust import TrustRegistry
from kawa.storage.db import connect
from kawa.storage.replication import frontier, pull

POLICY_DOC = os.path.join(os.path.dirname(__file__), os.pardir, "docs",
                          "durability-policy-v0.1.md")


def policy_digest() -> str:
    with open(POLICY_DOC, "rb") as f:
        return "sha256:" + hashlib.sha256(f.read()).hexdigest()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sanitize(dsn: str) -> str:
    return re.sub(r"password=\S+", "password=***", dsn)


def source_dsn() -> str:
    dsn = os.environ.get("KAWA_SOURCE_DSN")
    if not dsn:
        raise RuntimeError(
            "KAWA_SOURCE_DSN is not set and the replica loop refuses to guess a "
            "source (the 12A fail-closed doctrine, applied to the pull path). "
            "Name the origin node's database explicitly.")
    return dsn


def stub_counts(conn) -> tuple[int, int]:
    """(materialized, stubs) held locally — finding 4: stubs are reported, never blended."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FILTER (WHERE materialized), "
                    "count(*) FILTER (WHERE NOT materialized) FROM events")
        m, s = cur.fetchone()
    return m, s


def run_cycle(dest, source, *, node_ref: str, actor_ref: str, keys_path: str,
              trust_path: str, credential_path: str, status_file: str,
              scopes: tuple[str, ...], source_trust_path: str | None = None,
              puller_node: str | None = None) -> dict:
    """One pull cycle against open connections. Returns the status dict.
    Factored for the test suite — the CLI wraps it with connect() + exit code."""
    cred = load_or_create_local_node(credential_path, node_ref=node_ref)
    keys = PublicKeyRegistry(keys_path)
    keys.register(cred.signing_key_ref, cred.public_pem())
    trust = TrustRegistry(trust_path)
    source_trust = TrustRegistry(source_trust_path) if source_trust_path else None

    pdg = policy_digest()
    status: dict = {"ts": _now(), "policy_digest": pdg}
    try:
        src_frontier = frontier(source)
        report = pull(dest, source, keys=keys, trust=trust, clock=HLC(node=node_ref),
                      source_trust=source_trust, puller_node=puller_node, scopes=scopes)
        after = frontier(dest)
        lag = {origin: head - after.get(origin, 0) for origin, head in src_frontier.items()}
        reasons = sorted({r.reason for r in report.rejected})
        materialized, stubs = stub_counts(dest)
        ok = not report.rejected
        status.update({"ok": ok, "admitted": len(report.admitted),
                       "rejected": len(report.rejected), "reject_reasons": reasons,
                       "source_frontier": src_frontier, "local_frontier": after,
                       "lag": lag, "materialized": materialized, "stubs": stubs})

        kawa = Kawa(dest, identity=IdentityContext.from_local_node(cred, actor_ref=actor_ref))
        binding = dict(source_ref=f"kawa-replica://{node_ref}",
                       content_digest="sha256:" + hashlib.sha256(json.dumps(
                           {"source": src_frontier, "local": after},
                           sort_keys=True).encode()).hexdigest(),
                       fetched_at=status["ts"], policy_digest=pdg)
        kawa.record_observation(
            "replication_frontier_lag", value_number=float(sum(lag.values())),
            method="metric_read",
            source_revision=(f"lag={json.dumps(lag, sort_keys=True)} "
                             f"admitted={len(report.admitted)} rejected={len(report.rejected)} "
                             f"stubs={stubs} policy_digest={pdg}"),
            **binding)
        if report.rejected:
            first = report.rejected[0]
            kawa.record_observation(
                "replication_admission_reject", value_number=float(len(report.rejected)),
                method="metric_read",
                source_revision=(f"reasons={','.join(reasons)} "
                                 f"first={first.origin_node}/{first.origin_seq}:{first.reason} "
                                 f"policy_digest={pdg}"),
                **binding)
        dest.commit()
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
    ap.add_argument("--keys", default=os.path.expanduser("~/.kawa/keys.json"))
    ap.add_argument("--trust", default=os.path.expanduser("~/.kawa/trust.json"))
    ap.add_argument("--credential", default=os.path.expanduser("~/.kawa/node_credential.json"))
    ap.add_argument("--status-file",
                    default=os.path.join(nodehealth.status_dir(), "replica-pull.status"))
    ap.add_argument("--scopes", default="fleet",
                    help="comma-separated requested scopes (the RETAIN set)")
    ap.add_argument("--source-trust", default=os.environ.get("KAWA_SOURCE_TRUST"),
                    help="mirror of the source's serving registry (grants = offer set)")
    ap.add_argument("--puller-node", default=None,
                    help="node_ref the offer is computed for (default: this node)")
    args = ap.parse_args()
    node = os.environ.get("KAWA_NODE") or os.uname().nodename.split(".")[0]
    try:
        with connect() as dest, psycopg.connect(source_dsn(), autocommit=False) as source:
            status = run_cycle(
                dest, source, node_ref=node, actor_ref="replica-pull",
                keys_path=args.keys, trust_path=args.trust, credential_path=args.credential,
                status_file=args.status_file,
                scopes=tuple(s for s in args.scopes.split(",") if s),
                source_trust_path=args.source_trust,
                puller_node=args.puller_node or node)
    except Exception as exc:
        print(f"replica pull FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(status, indent=2))
    return 0 if status["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
