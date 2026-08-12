"""Occurrence identity for Work execution (#111 8D — the step-7 restart-duplicate debt).

The minimal, honest slice of the operation-effect-identity keystone (§2): ONLY the plan-node
minter, ONLY gating Result *recording*. The full keystone (verb registry, drift-CI projection
freeze, client/schedule minters, actuator CommitToken) stays deferred and named — external
side-effect firing between execution and Result recording is NOT closed by this module
(wake-pull-coordination §"honest split").

The key is a content address over durable cause bytes only — no node id, no log position,
no wall-clock, no nonce (keystone R1) — so any node, any time, re-derives the identical key
for the identical attempt:

    occurrence_key = H_OK("plan", plan_ref, work_ref, causal_prior_result_ref)

`causal_prior_result_ref` is the attempt-lineage coordinate: the event_id of the Result this
attempt is retrying AFTER (None asserts a FIRST attempt). It discriminates the three cases
(#111 rev 2 (a)): a duplicate re-record of the same attempt re-derives the same key (caught);
a crash-before-Result retry re-derives the same key but no recorded Result holds it yet
(records normally); a deliberate retry after failure carries the failure Result's event_id —
different bytes, new key (records normally).

**BC-1 (binding).** `causal_prior_result_ref` comes from the retry TRIGGER — the dispatch /
wake context that decided to re-execute — never from an implicit local query at derivation
time. A replica whose local log has not yet received the prior Result would silently derive
the first-attempt key (false merge on arrival, the r2 counterexample); passing None is
therefore an assertion of "first attempt", never a fallback for "locally unknown". This
module takes the value as an explicit argument precisely so no DB handle is available to
fall back on.
"""
from __future__ import annotations

from kawa.domain.ids import digest


def h_ok(minter_tag: str, *coordinate: object) -> str:
    """Content address over (minter, coordinate) — keystone §2 H_OK."""
    return digest({"minter": minter_tag, "coordinate": list(coordinate)})


def work_occurrence_key(*, plan_ref: str, work_ref: str,
                        causal_prior_result_ref: str | None) -> str:
    """The plan-node occurrence key for one Work execution attempt. See module docstring
    (and BC-1) for the meaning and sourcing of `causal_prior_result_ref`."""
    return h_ok("plan", plan_ref, work_ref, causal_prior_result_ref)
