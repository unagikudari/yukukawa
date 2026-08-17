-- Console phase 1 step 4: annotate evidence edges with their endpoints' event
-- kinds, resolved BY THE REDUCER at refresh (the screen reads the projection
-- only and never joins raw event tables). NULL = the endpoint event is not
-- held locally — rendered as "not held", never guessed.
ALTER TABLE evidence_provenance ADD COLUMN IF NOT EXISTS source_kind text;
ALTER TABLE evidence_provenance ADD COLUMN IF NOT EXISTS target_kind text;
