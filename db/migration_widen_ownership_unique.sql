-- Widen cms_ownership_records unique constraint to include owner_role.
-- The CMS ownership dataset can list the same owner against the same CCN
-- on the same ownership_start_date under multiple distinct roles (e.g.
-- "5% OR GREATER DIRECT OWNERSHIP INTEREST" and "OPERATIONAL/MANAGERIAL
-- CONTROL" on the same date) — the old (ccn, owner_name, ownership_start_date)
-- constraint collapses those into one ON CONFLICT target within a single
-- batched INSERT, which Postgres rejects with a CardinalityViolation.

ALTER TABLE cms_ownership_records
    DROP CONSTRAINT cms_ownership_records_ccn_owner_name_ownership_start_date_key;

ALTER TABLE cms_ownership_records
    ADD CONSTRAINT cms_ownership_records_ccn_owner_name_start_role_key
    UNIQUE (ccn, owner_name, ownership_start_date, owner_role);
