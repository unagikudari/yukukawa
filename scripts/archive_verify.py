"""Re-verify an archive file and RECORD the outcome (#113 9c — §12.3: "commitment existence
does not prove durability"; the proof is a decaying, recorded Observation, never an assumption).

Usage:  KAWA_DSN=dbname=kawa python scripts/archive_verify.py <archive-file> [<keys.json>]

Records `archive_restore_ok` (value_bool) with the #98 source-binding tuple over the file —
a corrupted archive yields a recorded FAILURE Observation, not silence. The operator/policy
loop owns the cadence (re-run this; the Console surfaces the latest proof age)."""
from __future__ import annotations

import datetime
import hashlib
import os
import sys

from kawa.application.services import Kawa
from kawa.domain.credential import PublicKeyRegistry
from kawa.domain.identity import IdentityContext
from kawa.storage.archive import ArchiveVerificationError, verify_archive_file
from kawa.storage.db import connect


def verify_and_record(kawa: Kawa, path: str, keys: PublicKeyRegistry) -> bool:
    with open(path, "rb") as f:
        content_digest = "sha256:" + hashlib.sha256(f.read()).hexdigest()
    fetched_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        verify_archive_file(path, keys=keys)
        ok, note = True, None
    except ArchiveVerificationError as exc:
        ok, note = False, str(exc)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # a file corrupted below the JSON/structure level must STILL record a failure
        # Observation — never crash into silence (#117 review; the 9c claim itself)
        ok, note = False, f"unreadable archive: {type(exc).__name__}: {exc}"
    kawa.record_observation(
        "archive_restore_ok", value_bool=ok, method="file_digest",
        source_ref=f"file://{os.path.abspath(path)}", source_revision=note or "verified",
        content_digest=content_digest, fetched_at=fetched_at,
    )
    return ok


if __name__ == "__main__":
    path = sys.argv[1]
    keys = PublicKeyRegistry(sys.argv[2] if len(sys.argv) > 2 else
                             os.path.expanduser("~/.kawa/keys.json"))
    with connect() as conn:
        kawa = Kawa(conn, identity=IdentityContext.from_credential_file(
            actor_ref="archive-verify"))
        ok = verify_and_record(kawa, path, keys)
    print(f"{'OK' if ok else 'FAILED'} {path} (Observation recorded)")
    sys.exit(0 if ok else 1)
