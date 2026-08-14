-- Kawa step 10 PR 3 (#118): CP-gate integration state, security plane (never reduced,
-- never in rebuild()'s TRUNCATE set, never counted by the frontier).

-- The policy SoT (10D): content-addressed lineage. Rows are established/superseded ONLY
-- through receipt-gated CP operations; the verifier-facing head is the row with no
-- superseded_by. Bundles are immutable bytes — supersession appends, never edits.
CREATE TABLE IF NOT EXISTS policy_lineage (
    policy_digest              text PRIMARY KEY,
    canonical_bundle           text NOT NULL,
    established_receipt_digest text NOT NULL,     -- authority.receipt event_id that authorized it
    prior_policy_digest        text,
    superseded_by              text,              -- forward pointer; NULL = current head
    established_at             timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- PR #121 review: the lineage has AT MOST ONE head, enforced by the database — two
-- concurrent establishments cannot both land as current (the loser errors instead of
-- creating the multi-head state that would freeze the whole authority mechanism).
CREATE UNIQUE INDEX IF NOT EXISTS policy_lineage_single_head
    ON policy_lineage ((true)) WHERE superseded_by IS NULL;

-- rev 2 (b): the physically possible S1 violation, DETECTED and recorded — two
-- facially-valid genesis records for one authority key. Both lines stay blocked
-- (the verifier answers INVALID for both); this row is the named administrative
-- conflict an operator (or a future recovery ceremony) must confront.
CREATE TABLE IF NOT EXISTS security_authority_conflict (
    authority_key text        NOT NULL,
    digest_a      text        NOT NULL,
    digest_b      text        NOT NULL,
    detected_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (authority_key, digest_a, digest_b)
);
