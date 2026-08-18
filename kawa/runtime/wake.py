"""The wake cue — the ONLY text this system ever puts into a runtime's PTY
(#200 rev 3 §REAL-3).

Kawa's runtime doctrine is "wake the runtime; do not inject the
conversation" (#108). The cue exists to make an agent look, not to tell it
what to do: it carries no Work reference, no objective, no plan text, and no
identifiers at all, because the moment a cue can carry content someone will
put content in it and the pulled state stops being authoritative.

`WAKE_CUE` is a module constant with no format placeholders and no builder
function. Anything that wants to "just add the work_ref" has to change this
file and face the test that asserts the total bytes reaching a runtime
across a whole launch equal exactly this string.
"""
from __future__ import annotations

WAKE_CUE = "Kawa work is available; pull current state with scripts/brief.py."
