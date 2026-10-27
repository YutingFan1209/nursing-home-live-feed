"""
Missouri UCC debtor search in a live browser session (Claude in Chrome).

bsd.sos.mo.gov needs the person's free account to search; see ucc/mo.py.
Its results carry no secured party, so the runner also saves the scanned
image of each recent filing, and ingest has Claude read the secured party
off it (one short paid call per image).

    # 1. Print the names to search (CHOW buyers, new licensees, MO deal operators)
    venv/bin/python3 scripts/mo_ucc_browser.py names > /tmp/mo_names.json
    # 2. In the person's logged-in Chrome, on LienSearch.aspx: set
    #    window.MO_NAMES and paste scripts/mo_ucc_browser_search.js. It saves
    #    mo_ucc_<timestamp>.json parts to Downloads, 25 names each.
    # 3. Load the results (reads each saved image with Claude)
    venv/bin/python3 scripts/mo_ucc_browser.py ingest ~/Downloads/mo_ucc_*.json

    # A run that stopped early: resume from the first unsearched name
    venv/bin/python3 scripts/mo_ucc_browser.py names --after ~/Downloads/mo_ucc_*.json
"""
import base64
import json
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2

import main
from config import get_config
from pipeline.pdf_text import _first_pages
from scraper.ucc import _filing_to_article, _union_names
from ucc.mo import rows_to_filings, search_name, secured_party_from_pdf


def _names(conn) -> list[str]:
    names = _union_names(main._buyer_names(conn, "MO"), main._get_known_operator_names(conn, "MO"))
    out, seen = [], set()
    for n in names:
        s = search_name(n)
        if len(s) >= 4 and s.upper() not in seen:
            seen.add(s.upper())
            out.append(s)
    return out


def _load(paths: list[str]) -> dict:
    parts = [json.load(open(p)) for p in paths]
    return {"results": [r for p in parts for r in p["results"]],
            "stopped": next((p["stopped"] for p in parts if p.get("stopped")), None)}


def ingest(conn, paths: list[str]) -> None:
    data = _load(paths)
    source_id = main.ensure_ucc_source(conn)
    images = filings = articles = deals = 0
    for r in data["results"]:
        for row in r["rows"]:
            # Already loaded (scraper.ucc's article URL): don't pay to read it again
            if row.get("doc") and not main._article_exists(f"ucc://MO/{row['fileNo']}", conn):
                images += 1
                # The parties are on page 1; later pages are collateral exhibits
                _, first = _first_pages(base64.b64decode(row["doc"]), 2)
                row["parsed"] = secured_party_from_pdf(first, row["fileNo"])
        for f in rows_to_filings(r["rows"], r["query"]):
            filings += 1
            art = _filing_to_article(f)
            if main._article_exists(art["url"], conn):
                continue
            art["source_id"] = source_id
            articles += 1
            deals += main.process_article(art, conn)
            conn.commit()
    rows = sum(len(r["rows"]) for r in data["results"])
    print(f"{len(data['results'])} names searched (stopped: {data.get('stopped')}) -> {rows} filings, "
          f"{images} images read -> {filings} with a secured party -> {articles} new -> {deals} deals")


if __name__ == "__main__":
    conn = psycopg2.connect(get_config().database_url)
    if sys.argv[1] == "names":
        names = _names(conn)
        if "--after" in sys.argv:
            done = {r["query"] for r in _load(sys.argv[sys.argv.index("--after") + 1:])["results"]}
            names = [n for n in names if n not in done]
        print(json.dumps(names))
    elif sys.argv[1] == "ingest":
        ingest(conn, sys.argv[2:])
