"""Step 12B: custodian attestation backfill — sign the unsigned history, once, audited.

The dogfood log predates sign-at-birth: every pre-12B event carries signature NULL,
and cross-node admission (correctly) rejects unsigned events. The custodian attaches
its attestation to the events it already holds. Identity is untouched — signature is
custody metadata outside `event_hash` (sql/0015 permits exactly this monotone
NULL→value transition and nothing else).

Usage (one origin per invocation, ONE atomic transaction):
  KAWA_DSN=dbname=kawa python scripts/attest_backfill.py --origin panoplia \
      [--credential ~/.kawa/node_credential.json] [--keys ~/.kawa/keys.json]
  KAWA_DSN=dbname=kawa python scripts/attest_backfill.py --origin test --ephemeral \
      [--keys ~/.kawa/keys.json]

--credential: sign with a persisted credential whose node_ref MUST equal --origin
  (the receiver's forged_origin check binds key→origin; a mismatch would poison
  the whole stream at admission).
--ephemeral (#129 12B review, finding 2): mint the signing key IN MEMORY, sign the
  historical stream, and DISCARD the private half — nothing is written but the
  public key (registered into --keys so verification resolves). For terminated
  origins (`local`, `test`) no future event may ever be signed: the key that could
  sign one no longer exists, and the printed receiver seal (`revoke from_seq=head+1`)
  closes the trust plane too.

Idempotent: already-signed events are skipped; a second run over a fully signed
origin is a no-op (exit 0, nothing emitted). The audit Observation
(`attestation_backfill`) commits IN THE SAME transaction as the signatures.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kawa.application.services import Kawa
from kawa.domain.credential import (
    Ed25519NodeCredential,
    PublicKeyRegistry,
    _fingerprint,
    load_or_create_local_node,
)
from kawa.domain.identity import IdentityContext
from kawa.storage.db import connect


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ephemeral_credential(origin: str) -> Ed25519NodeCredential:
    """A signing credential that exists only for this process (finding 2): the
    private key is generated, used, and dropped with the process — never persisted."""
    priv = Ed25519PrivateKey.generate()
    return Ed25519NodeCredential(node_ref=origin, signing_key_ref=_fingerprint(priv.public_key()),
                                 _private=priv)


def backfill(conn, *, origin: str, signer: Ed25519NodeCredential, keys: PublicKeyRegistry,
             audit_identity: IdentityContext) -> dict:
    """Sign every unsigned event of `origin` + emit the audit Observation, one transaction.
    Returns the audit summary. Caller commits."""
    if signer.node_ref != origin:
        raise SystemExit(f"credential node_ref {signer.node_ref!r} != --origin {origin!r} — "
                         "the receiver's forged_origin check would reject the stream")
    with conn.cursor() as cur:
        cur.execute("SELECT origin_seq, self_hash, signing_key_ref FROM events "
                    "WHERE origin_node=%s ORDER BY origin_seq FOR UPDATE", (origin,))
        rows = cur.fetchall()
        if not rows:
            raise SystemExit(f"origin {origin!r} holds no events")
        targets = [(seq, sh) for seq, sh, kref in rows if kref is None]
        head = rows[-1][0]
        if not targets:
            return {"origin": origin, "signed": 0, "head": head, "note": "already fully signed"}
        for seq, self_hash in targets:
            cur.execute(
                "UPDATE events SET signature=%s, signing_key_ref=%s, signature_scheme=%s "
                "WHERE origin_node=%s AND origin_seq=%s",
                (signer.sign(self_hash), signer.signing_key_ref, signer.signature_scheme,
                 origin, seq))
    keys.register(signer.signing_key_ref, signer.public_pem())

    ids_digest = "sha256:" + hashlib.sha256(
        "\n".join(sh for _, sh in targets).encode()).hexdigest()
    lo, hi = targets[0][0], targets[-1][0]
    kawa = Kawa(conn, identity=audit_identity)
    kawa.record_observation(
        "attestation_backfill", value_number=float(len(targets)), method="command_exit",
        source_ref=f"kawa://origin/{origin}",
        source_revision=(f"origin={origin} seqs={lo}..{hi} count={len(targets)} "
                         f"key_ref={signer.signing_key_ref} scheme={signer.signature_scheme} "
                         f"head={head}"),
        content_digest=ids_digest, fetched_at=_now())
    return {"origin": origin, "signed": len(targets), "seqs": f"{lo}..{hi}", "head": head,
            "key_ref": signer.signing_key_ref}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", required=True)
    ap.add_argument("--credential", default=os.path.expanduser("~/.kawa/node_credential.json"))
    ap.add_argument("--keys", default=os.path.expanduser("~/.kawa/keys.json"))
    ap.add_argument("--ephemeral", action="store_true",
                    help="mint the signing key in memory and discard it after signing "
                         "(terminated origins only)")
    args = ap.parse_args()

    signer = (ephemeral_credential(args.origin) if args.ephemeral
              else load_or_create_local_node(args.credential))
    keys = PublicKeyRegistry(args.keys)
    # the custodian node signs the audit
    audit_identity = IdentityContext.from_credential_file(actor_ref="attest-backfill")

    with connect() as conn:
        summary = backfill(conn, origin=args.origin, signer=signer, keys=keys,
                           audit_identity=audit_identity)
        conn.commit()                                          # signatures + audit, atomically

    print(json.dumps(summary, indent=2))
    if args.ephemeral and summary.get("signed", 0) > 0:
        print(f"\nreceiver seal (finding 2 — terminated origin, run on every replica):\n"
              f"  trust.enroll({args.origin!r}, {summary['key_ref']!r})\n"
              f"  trust.revoke({summary['key_ref']!r}, from_seq={summary['head'] + 1})",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
