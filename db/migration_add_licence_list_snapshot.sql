-- db/migration_add_licence_list_snapshot.sql
--
-- State licence lists (MO data.mo.gov, CA CDPH) name each nursing home's
-- licensee. Where a state publishes no sale filing, a new licensee on a
-- CMS-listed home is the sale (scraper/licence_lists.py). This keeps the
-- licensee each home had at the last check, so every run diffs the new copy
-- against it.

CREATE TABLE IF NOT EXISTS licence_list_snapshot (
    state         text NOT NULL,
    ccn           text NOT NULL,
    licensee      text NOT NULL,
    licence_date  date,          -- MO licence effective date (CA's list has none)
    seen_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (state, ccn)
);

-- Rollback:
-- DROP TABLE IF EXISTS licence_list_snapshot;
