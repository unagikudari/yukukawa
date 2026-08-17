## Summary

An adversarial review of `kawa/retrieval.py` identified that retrieval queries across all backends (FTS lexical search, BFS neighborhood traversal, Vector similarity search, Anchor lookups, and Evidence extraction) lack `scope_ref` filtering. This allows a caller to retrieve and exfiltrate payloads belonging to scopes they are not authorized to view.

## Attack / Falsification Scenario (ADV-02)

1. Step 9b (`#113`, `sql/0010_envelope_v2.sql`) introduced envelope-v2 `scope_ref` and `scope_digest` commitments to isolate visibility across scopes (e.g. `fleet` vs restricted project scopes).
2. On a node holding materialized events across multiple scopes, a participant or local caller invokes `retrieve(conn, Intent(text_terms="confidential keyword"))`.
3. In `kawa/retrieval.py`:
   - `_exec_lexical`: queries `event_claim` and `current_plans` directly using `to_tsvector` without joining `events` on `scope_ref`.
   - `_exec_neighborhood`: traverses `event_links` without filtering node/event scopes.
   - `_exec_vector`: computes cosine similarity over `content_embedding` without scope restriction.
4. An agent or participant without grants to a restricted scope receives records containing confidential data in the returned `Bundle`.

## Guarantee (REAL)

- Retrieval MUST enforce viewer scope boundaries: any record returned by `retrieve()` MUST belong to an authorized scope (`scope_ref IN (viewer_scopes)` or legacy `scope_ref IS NULL` / `fleet` where permitted).
- Out-of-scope records must be filtered out at the SQL query level before budget calculation and result aggregation.

## Proposed Design / Fix

1. In `kawa/retrieval.py`:
   - Add `authorized_scopes: frozenset[str]` to `Intent` (or pass as a parameter to `retrieve()`).
   - In `_exec_lexical`, join `events e ON e.event_id = ec.event_id` and add `WHERE (e.scope_ref = ANY(%s) OR e.scope_ref IS NULL)`.
   - In `_exec_neighborhood`, `_exec_evidence`, `_exec_anchor`, and `_exec_vector`, ensure only events within `authorized_scopes` are queried and expanded.
2. In `kawa/application/services.py`:
   - Pass participant's authorized scopes from `ParticipantSession` into retrieval invocations.
3. Tests:
   - Add multi-scope retrieval test in `tests/test_retrieval.py` asserting that restricted-scope claims are omitted when searching under default `fleet` grants.

## Scope Boundary

- REAL: Database-level scope filtering for all retrieval query classes.
- DEFERRED: Cryptographic envelope encryption at rest.

