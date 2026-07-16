-- Switch cms_ownership_records off the raw CMS "All Owners" enrollment
-- feed (keyed by PECOS Enrollment ID, which is NOT a CMS Certification
-- Number and can't be joined to cms_facilities.ccn at all) onto the
-- Provider Data Catalog "NH_Ownership_*.csv" export, which carries a real
-- CCN, the correct facility state (not owner mailing-address state), and
-- human-readable Owner Type ("Individual"/"Organization").
--
-- The new source has no stable per-owner associate ID, but
-- (ccn, owner_name, ownership_start_date, owner_role) is confirmed
-- collision-free across the full June 2026 file (245,354 rows, 245,354
-- unique keys), so we drop back to that as the natural key.

ALTER TABLE cms_ownership_records
    DROP CONSTRAINT IF EXISTS cms_ownership_records_ccn_assoc_start_role_key;

ALTER TABLE cms_ownership_records
    ADD CONSTRAINT cms_ownership_records_ccn_owner_start_role_key
    UNIQUE (ccn, owner_name, ownership_start_date, owner_role);

-- Existing rows were loaded from the old (wrong-ID-space) enrollment feed —
-- their ccn values are PECOS Enrollment IDs, not CCNs, and would corrupt
-- joins against cms_facilities if left in place.
TRUNCATE TABLE cms_ownership_records;
