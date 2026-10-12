-- db/migration_add_ucc_individual_matches.sql
--
-- NY UCC filings against a person (the individual-owner search) matched to
-- the NY homes that person owns or controls in CMS ownership data. Filled by
-- pipeline/individual_owners.py. A filing against an owner of several homes
-- can't be tied to one of them, so the feed shows every match as a
-- "possible home" and only links straight to a home when there's exactly one.

CREATE TABLE IF NOT EXISTS ucc_individual_matches (
    deal_id        uuid NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    ccn            text NOT NULL,
    provider_name  text,
    owner_name     text NOT NULL,   -- CMS form, "LAST, FIRST"
    owner_role     text,
    match_type     text NOT NULL CHECK (match_type IN ('single_home', 'multi_home')),
    matched_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (deal_id, ccn)
);

-- Added after the table first shipped (2026-10-12): the filing's marker on
-- each of its rows. Unmatched filings have no rows.
ALTER TABLE ucc_individual_matches ADD COLUMN IF NOT EXISTS match_type text;
UPDATE ucc_individual_matches m SET match_type = CASE WHEN n = 1 THEN 'single_home' ELSE 'multi_home' END
FROM (SELECT deal_id, count(*) n FROM ucc_individual_matches GROUP BY deal_id) c
WHERE c.deal_id = m.deal_id AND m.match_type IS NULL;
ALTER TABLE ucc_individual_matches ALTER COLUMN match_type SET NOT NULL;
DO $$ BEGIN
  ALTER TABLE ucc_individual_matches ADD CONSTRAINT ucc_individual_matches_match_type_check
    CHECK (match_type IN ('single_home', 'multi_home'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
