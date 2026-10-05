"""
Load UCC lender-search results gathered by hand or in a live browser
session (e.g. scripts/or_ucc_lender_search.js for Oregon) into the
pipeline, with the same nursing home filter as automated lender search.

Input: one filing per line, "filing_number | date | secured party[ ; ...] |
debtor[ ; debtor...][ | portal record id]". Dates may be MM-DD-YYYY or
YYYY-MM-DD; the first secured party and first debtor are used. The record
id (OH's entityId) becomes the per-filing deep link.

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

PORTALS = {"OR": "https://secure.sos.state.or.us/ucc/searchHome.action",
           "OH": "https://ucc.ohiosos.gov/search"}


def _date(s: str):
    for fmt in ("%m-%d-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unparseable date {s!r}")


def main_(state: str, path: str) -> None:
    conn = psycopg2.connect(get_config().database_url)
    names = main._cms_healthcare_names(conn, state) | {
        norm_name(n) for n in get_chow_operator_names(state) + main._get_known_operator_names(conn, state)}
    filings = []
    for line in open(path):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4 or not parts[0]:
            continue
        raw = {"query_name": f"{state} lender search (browser session)", "secured_parties": parts[2]}
        if len(parts) > 4 and parts[4]:
            raw["internal_id"] = parts[4]
        filings.append(UCCFiling(
            state=state, debtor_name=parts[3].split(" ; ")[0], secured_party_name=parts[2].split(" ; ")[0],
            filing_number=parts[0], filing_date=_date(parts[1]),
            filing_type="UCC-1", source_url=PORTALS.get(state), raw=raw,
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
