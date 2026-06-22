-- db/migration_add_ucc_support.sql
--
-- Adds support for both UCC integration modes confirmed 6/17:
--   1. CONFIRMATION — a UCC-1 filing corroborates a deal you already have
--      from CHOW/EDGAR/Alerts (existing deal row, just gets enriched)
--   2. NEW SIGNAL — a UCC-1 filing is the *first* evidence of a possible
--      acquisition, with no matching CHOW/EDGAR deal yet (new deal row,
--      flagged unconfirmed)
--
-- ASSUMPTIONS — adjust column names/types to match your real schema.sql,
-- which I don't have a copy of. Written against the table/column names
-- described in the skill notes (deals, sources, operators).

BEGIN;

-- 1. Mark which deals originated from / were touched by a UCC filing,
--    and at what confidence. Doesn't replace your existing `deals` table,
--    just extends it.
ALTER TABLE deals
    ADD COLUMN IF NOT EXISTS ucc_confirmed boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS confidence_level varchar(20) DEFAULT 'confirmed';
    -- confidence_level: 'confirmed' (existing CHOW/EDGAR-backed deals,
    -- unchanged default) | 'unconfirmed' (UCC-only, no CHOW match yet)

-- 2. Raw UCC filings table — every filing you pull gets stored here
--    regardless of routing outcome, so you have full audit history and
--    can re-run matching later as more CHOW data arrives.
CREATE TABLE IF NOT EXISTS ucc_filings (
    id              SERIAL PRIMARY KEY,
    state           varchar(2) NOT NULL,
    filing_number   varchar(100) NOT NULL,
    debtor_name     text NOT NULL,
    secured_party_name text NOT NULL,
    filing_date     date,
    lapse_date      date,
    filing_type     varchar(20) DEFAULT 'UCC-1',
    collateral_description text,
    status          varchar(20) DEFAULT 'active',
    lender_category varchar(30),          -- from lender_classifier.py: real_estate | private_equity | bank_general | equipment_vendor | unknown
    lender_confidence numeric(3,2),       -- classifier's confidence score
    is_acquisition_relevant boolean DEFAULT true,
    source_url      text,
    dedup_hash      varchar(64) UNIQUE NOT NULL,  -- from UCCFiling.dedup_hash()
    -- Routing outcome
    matched_deal_id integer REFERENCES deals(id),  -- NULL if routed as new signal
    routing_decision varchar(20),          -- 'confirmation' | 'new_signal' | 'excluded'
    created_at      timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ucc_filings_debtor ON ucc_filings (lower(debtor_name));
CREATE INDEX IF NOT EXISTS idx_ucc_filings_matched_deal ON ucc_filings (matched_deal_id);

COMMIT;

-- Rollback if needed:
-- BEGIN;
-- DROP TABLE IF EXISTS ucc_filings;
-- ALTER TABLE deals DROP COLUMN IF EXISTS ucc_confirmed, DROP COLUMN IF EXISTS confidence_level;
-- COMMIT;
