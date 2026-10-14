"""
Ohio UCC debtor search in a live browser session (Claude in Chrome).

ucc.ohiosos.gov blocks Playwright outright (memory oh-ucc-ip-blocked); the
person's own Chrome works. scripts/oh_ucc_lender_search.js covers lenders;
this covers the CHOW operator names the automated OH step would search.

    # 1. Print the names to search
    venv/bin/python3 scripts/oh_ucc_browser.py names > /tmp/oh_names.json
    # 2. In the person's Chrome, follow scripts/oh_ucc_browser_search.js:
    #    XHR patch, one manual Debtor/Organization search, set window.OH_NAMES,
    #    run it. It saves oh_ucc_<timestamp>.json to Downloads. The terminal
    #    can't read ~/Downloads (macOS privacy), so the person drags the file
    #    into the project folder.
    # 3. Load the results
    venv/bin/python3 scripts/oh_ucc_browser.py ingest oh_ucc_....json

    # A run that stopped early: resume from the first unsearched name
    venv/bin/python3 scripts/oh_ucc_browser.py names --after oh_ucc_....json
"""
import json
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2

import main
from config import get_config
from scraper.chow import get_chow_operator_names
from scraper.ucc import _filing_to_article
from ucc.oh_playwright import rows_to_filings


def _names() -> list[str]:
    return sorted({n.strip() for n in get_chow_operator_names("OH") if n and n.strip()}, key=str.upper)


def ingest(conn, path: str) -> None:
    data = json.load(open(path))
    filings = [f for r in data["results"] for f in rows_to_filings(r["query"], r["rows"])]
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
    print(f"{len(data['results'])} names searched (stopped: {data.get('stopped')}) -> {len(filings)} filings "
          f"-> {articles} new -> {deals} deals")


if __name__ == "__main__":
    if sys.argv[1] == "names":
        names = _names()
        if "--after" in sys.argv:
            done = {r["query"] for r in json.load(open(sys.argv[sys.argv.index("--after") + 1]))["results"]}
            names = [n for n in names if n not in done]
        print(json.dumps(names))
    elif sys.argv[1] == "ingest":
        ingest(psycopg2.connect(get_config().database_url), sys.argv[2])
