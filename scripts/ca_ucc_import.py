"""
Load California's weekly UCC bulk data files (ucc/ca_download.py).

A person requests each week in bizfile Online (Data Requests -> + New Data
Request -> "UCC Bulk Order - Weekly Data & Images" -> Data) and drops the
DataRequest0x....zip files in data/. Not "BE Bulk Order": those zips hold
Agents/Filings/Principals for new business entities, not UCC filings.

    venv/bin/python3 scripts/ca_ucc_import.py data/DataRequest0x*.zip

Filings already loaded are skipped, so re-running over the same files is
safe. First load 2026-10-14: nine weeks (2026-08-17 to 10-11).
"""
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2

import main
from config import get_config
from scraper.chow import get_chow_operator_names
from scraper.ucc import _filing_to_article
from ucc.ca_download import load_week
from ucc.lender_search import norm_name


def run(conn, paths: list[str]) -> None:
    names = main._cms_healthcare_names(conn, "CA") | {
        norm_name(n) for n in get_chow_operator_names("CA") + main._get_known_operator_names(conn, "CA")}
    source_id = main.ensure_ucc_source(conn)
    filings = [f for p in paths for f in load_week(p, names)]
    articles = deals = 0
    for f in filings:
        art = _filing_to_article(f)
        if main._article_exists(art["url"], conn):
            continue
        art["source_id"] = source_id
        articles += 1
        deals += main.process_article(art, conn)
        conn.commit()
    print(f"{len(paths)} weekly files -> {len(filings)} nursing home filings -> {articles} new -> {deals} deals")


if __name__ == "__main__":
    run(psycopg2.connect(get_config().database_url), sys.argv[1:])
