-- State certificate-of-need / ownership-notice filings (scraper/con_al.py)
-- get their own source_type so the site can label and filter them.
--
-- Also lists 'ucc' and 'chow': both were added to the live DB's constraint
-- by hand and never made it into schema.sql, so a fresh install following
-- README's Setup section rejected every UCC and CHOW source insert.
ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_source_type_check;
ALTER TABLE sources ADD CONSTRAINT sources_source_type_check
    CHECK (source_type IN ('rss', 'edgar', 'googlenews', 'manual', 'ucc', 'chow', 'con'));
