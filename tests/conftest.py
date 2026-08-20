"""Step 12A test fence, conftest layer (#129 rev 3 F1 — defense-in-depth; the
load-bearing layer is the postgres role fence, proven by the negative control).

- pytest runs under the FENCED credential by default: the test DSN env vars are
  pinned to the `kawa_test` role over TCP unless the operator overrides them.
- any inherited KAWA_DSN is DROPPED before tests import code: code under test
  that reaches for the dogfood default meets `connect()`'s fail-closed refusal.
- a test DSN naming the dogfood database is a hard configuration error.
"""
from __future__ import annotations

import os
import re

_FENCED = "user=kawa_test password=kawa_test host=127.0.0.1"

os.environ.setdefault("KAWA_TEST_DSN_A", f"dbname=kawa_test_a {_FENCED}")
os.environ.setdefault("KAWA_TEST_DSN_B", f"dbname=kawa_test_b {_FENCED}")

os.environ.pop("KAWA_DSN", None)     # tests never inherit a live target

for _var in ("KAWA_TEST_DSN_A", "KAWA_TEST_DSN_B"):
    if re.search(r"dbname=kawa(\s|$)", os.environ[_var]):
        raise RuntimeError(
            f"{_var} names the dogfood database — the test fence refuses to run "
            "(#129 12A). Point it at a kawa_test_* database.")


# --- filesystem fence (2026-08-20) -------------------------------------------
# The DB fence above stops a test reaching the dogfood database. It did NOT stop
# a test reaching the operator's REAL status directory: a collector test whose
# path came from a frozen default argument wrote `node: "test", ok: false` into
# ~/.kawa/status/goatcounter.status, where the session brief reads it as a live
# failure of a production resident.
#
# The redirect is the whole fence: KAWA_STATUS_DIR is hard-set to a scratch
# directory before any test imports code, and every status path in the tree
# resolves through kawa.nodehealth.status_dir(), which honours it.
#
# The first attempt at a second, detecting layer compared the real directory
# before and after the suite. That cannot work here and was removed: on a
# resident node the live kawa-supervisor rewrites supervisor.status every tick,
# so a 2.5-minute suite ALWAYS sees it change and the fence fires on production
# doing its job. test_status_paths_resolve_through_the_fence (tests/test_
# nodehealth.py) is the detector instead — static, and it cannot race.
import tempfile                                                   # noqa: E402

os.environ["KAWA_STATUS_DIR"] = tempfile.mkdtemp(prefix="kawa-test-status-")
