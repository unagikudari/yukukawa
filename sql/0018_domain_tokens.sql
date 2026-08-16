-- 0018: Phase A (#156) — managed domain tokens on Plan/Work defining events.
--
-- The principle-aware orientation channel (#142 Phase A) derives an anchor's
-- structural domain from typed declarations, never from free text. `scope` on
-- event_plan is a NON-semantic display field (#102 §6) and is deliberately NOT
-- the domain source (#156 r3-F1). `domain` holds a managed vocabulary token
-- validated fail-closed against registry/vocabulary.json `domains` at emit
-- time (unknown token -> rejected write; absent token -> legacy/UNMAPPED,
-- which orientation reports as a stated frontier).
--
-- Additive + nullable: existing events and their payload digests are
-- untouched (the payload serializer drops the key when unset, #102 round-2
-- constraint 2).

ALTER TABLE event_plan ADD COLUMN IF NOT EXISTS domain text;
ALTER TABLE event_work ADD COLUMN IF NOT EXISTS domain text;
