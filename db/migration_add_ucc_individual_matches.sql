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
    matched_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (deal_id, ccn)
);
