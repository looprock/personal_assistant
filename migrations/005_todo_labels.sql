-- Add labels column for system-assigned metadata (e.g. watch-pattern source tags).
-- Unlike tags (user-applied, {} = unprocessed), labels have no effect on
-- processed/unprocessed status and are never edited by the user.
ALTER TABLE todos ADD COLUMN IF NOT EXISTS labels TEXT[] DEFAULT '{}';

-- Migrate any watch-pattern values previously stored in tags → labels,
-- then remove them from tags so those todos return to unprocessed state.
UPDATE todos
SET
    labels = ARRAY(SELECT t FROM unnest(tags) AS t WHERE t = ANY(ARRAY['parentsquare', 'saviochs'])),
    tags   = COALESCE(ARRAY(SELECT t FROM unnest(tags) AS t WHERE t <> ALL(ARRAY['parentsquare', 'saviochs'])), '{}')
WHERE tags && ARRAY['parentsquare', 'saviochs'];
