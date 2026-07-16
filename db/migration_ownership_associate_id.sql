-- CMS's own "ASSOCIATE ID - OWNER" is the stable, authoritative identifier
-- for a specific owner record. Two distinct owners can otherwise share the
-- same (ccn, owner_name, ownership_start_date, owner_role) — e.g. two
-- people named "DAVID CHILDS" (one with a middle initial in the source
-- row, one without) tied to the same facility, date, and role — which
-- collapses under a name-based key and breaks the batched
-- ON CONFLICT DO UPDATE in cms/fetch_cms.py::_upsert_ownership.

ALTER TABLE cms_ownership_records
    ADD COLUMN IF NOT EXISTS owner_associate_id TEXT;

ALTER TABLE cms_ownership_records
    DROP CONSTRAINT IF EXISTS cms_ownership_records_ccn_owner_name_start_role_key;

ALTER TABLE cms_ownership_records
    ADD CONSTRAINT cms_ownership_records_ccn_assoc_start_role_key
    UNIQUE (ccn, owner_associate_id, ownership_start_date, owner_role);
