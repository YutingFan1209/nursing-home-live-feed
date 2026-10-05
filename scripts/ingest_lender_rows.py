"""
Load UCC lender-search results gathered by hand or in a live browser
session (e.g. scripts/or_ucc_lender_search.js for Oregon) into the
pipeline, with the same nursing home filter as automated lender search.

Input: one filing per line, "filing_number | MM-DD-YYYY | secured party |
debtor[ ; debtor...]". The first debtor is used.

    venv/bin/python3 scripts/ingest_lender_rows.py OR /path/to/rows.txt
"""
import sys
from datetime import datetime

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2

import main
from config import get_config
from scraper.chow import get_chow_operator_names
from scraper.ucc import _filing_to_article
from ucc.base import UCCFiling
from ucc.lender_search import filter_lender_hits, norm_name

PORTALS = {"OR": "https://secure.sos.state.or.us/ucc/searchHome.action"}


def main_(state: str, path: str) -> None:
    conn = psycopg2.connect(get_config().database_url)
    names = main._cms_healthcare_names(conn, state) | {
        norm_name(n) for n in get_chow_operator_names(state) + main._get_known_operator_names(conn, state)}
    filings = []
    for line in open(path):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4 or not parts[0]:
            continue
        filings.append(UCCFiling(
            state=state, debtor_name=parts[3].split(" ; ")[0], secured_party_name=parts[2],
            filing_number=parts[0], filing_date=datetime.strptime(parts[1], "%m-%d-%Y").date(),
            filing_type="UCC-1", source_url=PORTALS.get(state),
            raw={"query_name": f"{state} lender search (browser session)"},
        ))
    kept = filter_lender_hits(filings, names)
    source_id = main.ensure_ucc_source(conn)
    articles = deals = 0
    for f in kept:
        art = _filing_to_article(f)
        if not art["_ucc_classification"].is_acquisition_relevant or main._article_exists(art["url"], conn):
            continue
        art["source_id"] = source_id
        articles += 1
        deals += main.process_article(art, conn)
        conn.commit()
    print(f"{len(filings)} filings -> {len(kept)} nursing home -> {articles} new -> {deals} deals")


if __name__ == "__main__":
    main_(sys.argv[1].upper(), sys.argv[2])
