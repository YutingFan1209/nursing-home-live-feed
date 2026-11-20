-- A free copy of a paywalled story (pipeline/free_copy.py, 2026-11-20):
-- the page the card was also read from, shown as a second source
ALTER TABLE articles ADD COLUMN IF NOT EXISTS free_copy_url TEXT;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS free_copy_title TEXT;
