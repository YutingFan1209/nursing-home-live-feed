"""
Wisconsin UCC debtor search in a live browser session (Claude in Chrome).

wims.dfi.wi.gov answers every anonymous search with a reCAPTCHA; logged in
to the person's free DFI account it doesn't (2026-10-27). See ucc/wi.py.

    # 1. Print the names to search (CHOW buyers, new licensees, WI deal operators)
    venv/bin/python3 scripts/wi_ucc_browser.py names > /tmp/wi_names.json
    # 2. In the person's logged-in Chrome, follow scripts/wi_ucc_browser_search.js:
    #    XHR patch, one search by hand, set window.WI_NAMES, run it. It saves
    #    wi_ucc_<timestamp>.json to Downloads.
    # 3. Load the results
    venv/bin/python3 scripts/wi_ucc_browser.py ingest ~/Downloads/wi_ucc_....json

    # A run that stopped early: resume from the first unsearched name
    venv/bin/python3 scripts/wi_ucc_browser.py names --after ~/Downloads/wi_ucc_....json
"""
import json
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2

import main
from config import get_config
from scraper.ucc import _filing_to_article, _union_names
from ucc.wi import rows_to_filings, search_name


def _names(conn) -> list[str]:
    names = _union_names(main._buyer_names(conn, "WI"), main._get_known_operator_names(conn, "WI"))
    out, seen = [], set()
    for n in names:
        s = search_name(n)
        if len(s) >= 4 and s.upper() not in seen:
            seen.add(s.upper())
            out.append(s)
    return out


def ingest(conn, path: str) -> None:
    data = json.load(open(path))
    filings = [f for r in data["results"] for f in rows_to_filings(r["rows"], r["query"])]
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
    conn = psycopg2.connect(get_config().database_url)
    if sys.argv[1] == "names":
        names = _names(conn)
        if "--after" in sys.argv:
            done = {r["query"] for r in json.load(open(sys.argv[sys.argv.index("--after") + 1]))["results"]}
            names = [n for n in names if n not in done]
        print(json.dumps(names))
    elif sys.argv[1] == "ingest":
        ingest(conn, sys.argv[2])
