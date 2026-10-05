-- Every new-owner row from the CMS Ownership file that the ownership-change
-- detector (scraper/cms_owner_changes.py) has evaluated, whether or not it
-- became a deal, so each change is looked at exactly once.
CREATE TABLE IF NOT EXISTS cms_owner_change_seen (
    ccn           TEXT        NOT NULL,
    owner_name    TEXT        NOT NULL,
    start_date    DATE        NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ccn, owner_name, start_date)
);
