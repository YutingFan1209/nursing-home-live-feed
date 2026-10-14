"""
California UCC search in a live browser session (Claude in Chrome).

The automated CA step (ucc/ca_playwright.py) was refused on its first search
on 2026-10-12 and 10-13: Incapsula now turns away the auto-launched Chrome
profile outright. A person's own Chrome got PA and OH through the same kind
of block, so CA runs the same way.

    # 1. Print the names to search, from the saved resume offset
    venv/bin/python3 scripts/ca_ucc_browser.py names > /tmp/ca_names.json
    # 2. In the person's Chrome, open https://bizfileonline.sos.ca.gov/search/ucc,
    #    set window.CA_NAMES to that list and run scripts/ca_ucc_browser_search.js.
    #    It saves ca_ucc_<timestamp>.json to Downloads when it finishes or stops.
    #    The terminal can't read ~/Downloads (macOS privacy), so the person
    #    drags the file into the project folder.
    # 3. Load the results; advances the resume offset by the names searched
    venv/bin/python3 scripts/ca_ucc_browser.py ingest ca_ucc_....json

Lender search the same way, and the better default for CA: `lenders` prints
the 27 nursing home lenders (ucc/lender_search.LENDER_TERMS); set
window.CA_SEARCH_TYPE = 'SECURED_PARTY' before running the .js. CA's one
search box covers debtors and secured parties, so 27 searches return every
CA filing those lenders hold; `ingest` sees the search type in the file and
keeps recent filings with nursing home debtors. The debtor list (423 names)
can't get through in one session: on 2026-10-14 the portal throttled the
person's own Chrome after ~55 searches.
"""
import json
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2

import main
from config import get_config
from scraper.chow import get_chow_operator_names
from scraper.ucc import _filing_to_article
from ucc.ca_playwright import rows_to_filings
from ucc.lender_search import LENDER_TERMS, filter_lender_hits


def _names(conn) -> tuple[list[str], int]:
    # Same list main.py builds for the automated CA step
    return main._rotate_ca_names(conn, get_chow_operator_names("CA") + main._get_known_operator_names(conn, "CA"))


def ingest(conn, path: str) -> None:
    data = json.load(open(path))
    lender = data.get("search_type") == "SECURED_PARTY"
    filings = [f for r in data["results"] for f in rows_to_filings(r["query"], r["rows"], lender=lender)]
    if lender:
        filings = filter_lender_hits(filings, main._cms_healthcare_names(conn))
    # Before storing: new deals add CA names and would shift the list
    names, offset = _names(conn)
    source_id = main.ensure_ucc_source(conn)
    articles = deals = 0
    for f in filings:
        art = _filing_to_article(f)
        if main._article_exists(art["url"], conn):
            continue
        art["source_id"] = source_id
        articles += 1
        deals += main.process_article(art, conn)
        conn.commit()
    searched = len(data["results"])
    if lender:
        pass  # all 27 lenders each time, no resume offset
    elif names[:searched] != [r["query"] for r in data["results"]]:
        print("Searched names don't match the list from the saved offset; offset not advanced")
    else:
        main._save_offset(conn, main.CA_OFFSET_KEY, offset, searched, len(names), "CA")
    errors = [r["query"] for r in data["results"] if r.get("error")]
    if errors:
        print(f"{len(errors)} names got the portal's server error instead of results: {errors}")
    print(f"{searched} names searched (stopped: {data.get('stopped')}) -> {len(filings)} filings "
          f"-> {articles} new -> {deals} deals")


if __name__ == "__main__":
    conn = psycopg2.connect(get_config().database_url)
    if sys.argv[1] == "names":
        print(json.dumps(_names(conn)[0]))
    elif sys.argv[1] == "lenders":
        print(json.dumps(LENDER_TERMS))
    elif sys.argv[1] == "ingest":
        ingest(conn, sys.argv[2])
