-- db/migration_add_chow_seen_records.sql
--
-- CHOW "new since last check" used to filter on EFFECTIVE DATE > cutoff, but
-- that field lags real filing/publication time by 1.5-2 years (confirmed
-- 2026-09-22: the CHOW CSV's effective dates top out around Dec 2024 even
-- in a file published July 2026), so a rolling date-window filter could
-- never match anything once "today" drifted far enough past that -- CHOW-
-- sourced deal discovery had been silently producing zero results for a
-- long time. Replaces the date-window check with an explicit table of
-- every CHOW row's composite key already processed; each run diffs the
-- current CSV against this table instead of trusting effective dates to
-- behave like a real timestamp.

CREATE TABLE IF NOT EXISTS chow_seen_records (
    ccn             text NOT NULL,
    buyer_name      text NOT NULL,
    effective_date  text NOT NULL,  -- raw MM/DD/YYYY string from the CSV, kept as-is for an exact key match against future downloads
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ccn, buyer_name, effective_date)
);

-- Rollback:
-- DROP TABLE IF EXISTS chow_seen_records;
