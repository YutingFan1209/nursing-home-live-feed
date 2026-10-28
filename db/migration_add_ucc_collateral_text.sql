-- db/migration_add_ucc_collateral_text.sql
--
-- Adds the filing's collateral description, verbatim, to ucc_filings
-- (2026-10-28). Kept out of collateral_description, which main.py reads as
-- '|'-separated facility names. collateral_source says where the text came
-- from: 'page_text' (shown as text on the state's own filing page; RI first).

ALTER TABLE ucc_filings ADD COLUMN IF NOT EXISTS collateral_text TEXT;
ALTER TABLE ucc_filings ADD COLUMN IF NOT EXISTS collateral_source TEXT;

-- Rollback:
-- ALTER TABLE ucc_filings DROP COLUMN IF EXISTS collateral_text;
-- ALTER TABLE ucc_filings DROP COLUMN IF EXISTS collateral_source;
