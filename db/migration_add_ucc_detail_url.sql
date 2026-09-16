-- db/migration_add_ucc_detail_url.sql
--
-- Adds detail_url to ucc_filings: a real, one-click per-filing deep link
-- where the state portal supports one (currently only NY, via its
-- OnlineLienInformation?lienId=... page -- see ucc/ny_playwright.py).
-- NULL for states without a known per-filing permalink (KY, OH, PA, CA
-- today); those keep linking to the portal's search page instead.
--
-- Paired with wiring save_ucc_filings() into main.py:_process_ucc_filing
-- (previously dead code, never actually called by the live pipeline) so
-- this table starts getting populated for every filing going forward,
-- not just the ad hoc backfill scripts that used it before.

ALTER TABLE ucc_filings ADD COLUMN IF NOT EXISTS detail_url TEXT;

-- Rollback:
-- ALTER TABLE ucc_filings DROP COLUMN IF EXISTS detail_url;
