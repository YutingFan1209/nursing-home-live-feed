-- db/migration_add_ucc_confirmed.sql
--
-- Adds ucc_confirmed column to deals and extends the sources.source_type
-- CHECK constraint to include 'ucc' (required for _ensure_source in main.py).

ALTER TABLE deals ADD COLUMN IF NOT EXISTS ucc_confirmed boolean DEFAULT false;

-- Extend the source_type CHECK to include 'ucc' and 'chow' (both needed).
ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_source_type_check;
ALTER TABLE sources ADD CONSTRAINT sources_source_type_check
    CHECK (source_type IN ('rss', 'edgar', 'googlenews', 'manual', 'ucc', 'chow'));

-- Rollback:
-- ALTER TABLE deals DROP COLUMN IF EXISTS ucc_confirmed;
-- ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_source_type_check;
-- ALTER TABLE sources ADD CONSTRAINT sources_source_type_check
--     CHECK (source_type IN ('rss', 'edgar', 'googlenews', 'manual'));
