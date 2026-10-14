-- New California business entities that look like nursing home acquisition
-- vehicles (ucc/ca_entities.py), from the Secretary of State's free weekly
-- business entity bulk data. tier 'watch' names are matched as nursing home
-- debtors in the CA weekly UCC import; 'review' ones are listed for a person.
CREATE TABLE IF NOT EXISTS ca_entity_watch (
    entity_num        TEXT PRIMARY KEY,
    entity_name       TEXT NOT NULL,
    filed_on          DATE,
    entity_type       TEXT,
    type_of_business  TEXT,
    principal_city    TEXT,
    principal_state   TEXT,
    managers          TEXT,
    tier              TEXT NOT NULL CHECK (tier IN ('watch', 'review')),
    reason            TEXT,
    source_file       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ca_entity_watch_tier ON ca_entity_watch (tier);
