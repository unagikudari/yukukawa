## Summary

An adversarial review of `kawa/storage/authority_gate.py:resolve_fork` identified a fatal limitation where resolving a fork by adopting the rival branch (`chosen_head = rival_event_id`) is unconditionally rejected with `AuthorityRefused("rival_adoption_deferred")`. This leaves a node permanently frozen if it was on the incorrect branch of a fork.

## Attack / Falsification Scenario (ADV-03)

1. A node replicates a forked history and encounters a legitimate fork between its held branch and a rival branch, recording a freeze row in `security_fork_evidence`.
2. An Authority Cell issues a valid, signed `AuthorityReceipt` specifying that the rival branch is the legitimate head (`chosen_head == rival_event_id`).
3. An operator attempts to apply this receipt using `resolve_fork(conn, trust, origin_node=..., origin_seq=..., chosen_head=rival_event_id, receipt_event_id=...)`.
4. In `authority_gate.py:129-130`:
   ```python
   if chosen_head != held_event_id:
       raise AuthorityRefused("rival_adoption_deferred")
   ```
5. The operation fails. The node cannot switch to the winner branch or unfreeze the origin, resulting in a permanent partition / operational deadlock.

## Guarantee (REAL)

- When presented with a VALID `AuthorityReceipt` authorizing the adoption of the rival head, `resolve_fork` MUST:
  1. Revoke the key of the losing held branch (scoped to `from_seq=origin_seq`).
  2. Perform branch reconciliation: replace the held un-committed/divergent events starting from `origin_seq` with the rival branch events (or mark held events as superseded/quarantined).
  3. Clear the freeze in `security_fork_evidence` (`frozen=false`).
  4. Trigger a projection rebuild / update so local projections reflect the newly adopted canonical branch.

## Proposed Design / Fix

1. Complete the Step 10 D2 addendum for rival adoption in `kawa/storage/authority_gate.py`:
   - Implement the transaction that truncates/quarantines events after `origin_seq` from the invalidated held branch, inserts the rival events, and executes `reducers.rebuild(conn)`.
   - Update `security_fork_evidence` with `resolved_by = receipt_event_id` and `chosen_head = rival_event_id`.
2. Tests:
   - Add a test case in `tests/test_authority_gate.py` that verifies a node on the losing side of a fork successfully adopts the rival branch upon receiving a valid AuthorityReceipt and unfreezes the origin.

## Scope Boundary

- REAL: Single-node deterministic rival branch adoption under a valid CP receipt.
- DEFERRED: Automated distributed leader election without human/Authority-Cell intervention.

