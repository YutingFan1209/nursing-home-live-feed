-- db/migration_add_deal_merged_into.sql
--
-- pipeline/same_sale.py folds a duplicate deal into the one it keeps and
-- marks the duplicate dismissed. merged_into records which deal it went
-- into, so the site can still list the duplicate's source on that deal.
-- Rows merged before this column existed are backfilled from
-- data/same_sale_merges.jsonl by scripts/backfill_merged_into.py.

ALTER TABLE deals ADD COLUMN IF NOT EXISTS merged_into uuid REFERENCES deals(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_deals_merged_into ON deals (merged_into) WHERE merged_into IS NOT NULL;
