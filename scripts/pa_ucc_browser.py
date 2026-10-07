"""
Pennsylvania UCC debtor search in a live browser session (Claude in Chrome).

The automated PA step (ucc/pa_playwright.py) gets Incapsula-blocked after
~15 searches every run, whatever the gap between runs, so its lender search
eats each run and the 359-name debtor list never starts. A person's own
Chrome passes (2026-10-06: a search ran clean 20 min after the scraper was
blocked). Same pattern as OH and OR.

    # 1. Print the debtor names to search, from the saved resume offset
    venv/bin/python3 scripts/pa_ucc_browser.py names > /tmp/pa_names.json
    # 2. In the person's Chrome, open https://file.dos.pa.gov/search/ucc, set
    #    window.PA_NAMES to that list and run scripts/pa_ucc_browser_search.js.
    #    It saves pa_ucc_<timestamp>.json to Downloads when it finishes or stops.
    #    The terminal can't read ~/Downloads (macOS privacy), so the person
    #    drags the file into the project folder.
    # 3. Load the results; advances the resume offset by the names searched
    venv/bin/python3 scripts/pa_ucc_browser.py ingest pa_ucc_....json

Lender search the same way: `lenders` prints the 27 nursing home lenders
from their resume offset; set window.PA_SEARCH_TYPE = 'SECURED_PARTY' before
running the .js; `ingest` sees the search type in the file, keeps recent
filings with nursing home debtors (ucc/lender_search.filter_lender_hits)
and advances the lender offset. The automated lender search was blocked on
its first search (GREYSTONE) on 2026-10-07.

First run 2026-10-06: all 376 names in ~45 min (~5 s each), no block, 1,223
filings -> 706 new -> 232 deals.
"""
import json
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2

import main
from config import get_config
from scraper.chow import get_chow_operator_names
from scraper.ucc import _filing_to_article
from ucc.base import UCCFiling
from ucc.lender_search import LENDER_TERMS, filter_lender_hits
from ucc.pa_playwright import BASE_URL, _parse_date, _split_name


def _names(conn) -> tuple[list[str], int]:
    return main._rotate_names(
        conn, get_chow_operator_names("PA") + main._get_known_operator_names(conn, "PA"),
        main.PA_OFFSET_KEY, "PA")


def _lenders(conn) -> tuple[list[str], int]:
    return main._rotate_names(conn, LENDER_TERMS, main.PA_LENDER_OFFSET_KEY, "PA lender", keep_order=True)


def _filing(query: str, row_id: str, row: dict, search_type: str = "DEBTOR") -> UCCFiling:
    # Mirrors ucc/pa_playwright.py:_search_one
    status_raw = (row.get("STATUS") or "").lower()
    return UCCFiling(
        state="PA",
        debtor_name=_split_name(row.get("DEBTOR", "")),
        secured_party_name=_split_name(row.get("SEC_PARTY", "")),
        filing_number=row.get("RECORD_NUM", ""),
        filing_date=_parse_date(row.get("FILING_DATE", "")),
        lapse_date=_parse_date(row.get("LAPSE_DATE", "")),
        filing_type=row.get("RECORD_TYPE", ""),
        status="active" if "active" in status_raw and "inactive" not in status_raw else "inactive",
        source_url=BASE_URL + "/search/ucc",
        raw={"query_name": query, "record_id": row_id, "search_type": search_type},
    )


def ingest(conn, path: str) -> None:
    data = json.load(open(path))
    search_type = data.get("search_type", "DEBTOR")
    filings = [_filing(r["query"], rid, row, search_type) for r in data["results"] for rid, row in r["rows"].items()]
    if search_type == "SECURED_PARTY":
        filings = filter_lender_hits(filings, main._cms_healthcare_names(conn))
        names, offset = _lenders(conn)
        key, label = main.PA_LENDER_OFFSET_KEY, "PA lender"
    else:
        # Before storing: new deals add PA names and would shift the list
        names, offset = _names(conn)
        key, label = main.PA_OFFSET_KEY, "PA"
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
    if names[:searched] != [r["query"] for r in data["results"]]:
        print("Searched names don't match the list from the saved offset; offset not advanced")
    else:
        main._save_offset(conn, key, offset, searched, len(names), label)
    print(f"{searched} names searched (stopped: {data.get('stopped')}) -> {len(filings)} filings "
          f"-> {articles} new -> {deals} deals")


if __name__ == "__main__":
    conn = psycopg2.connect(get_config().database_url)
    if sys.argv[1] == "names":
        print(json.dumps(_names(conn)[0]))
    elif sys.argv[1] == "lenders":
        print(json.dumps(_lenders(conn)[0]))
    elif sys.argv[1] == "ingest":
        ingest(conn, sys.argv[2])
