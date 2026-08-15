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
