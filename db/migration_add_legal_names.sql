-- Each home's licensed company ("Legal Business Name" in CMS's monthly
-- Provider Information file), and every name seen for it. A new licensee
-- appears here months before CMS's CHOW file lists the sale (2026-10-21:
-- the September file already named the buyer for 111 of the 191 sales
-- CHOW first published on 10-19), so new names go into the UCC searches
-- (scraper/licensee_names.py).
ALTER TABLE cms_facilities ADD COLUMN IF NOT EXISTS legal_business_name TEXT;
CREATE TABLE IF NOT EXISTS cms_legal_names (
    ccn                 TEXT NOT NULL,
    legal_business_name TEXT NOT NULL,
    first_seen          DATE NOT NULL,
    PRIMARY KEY (ccn, legal_business_name)
);
