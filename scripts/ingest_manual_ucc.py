"""
scripts/ingest_manual_ucc.py

Ingest UCC-1 filings found via manual portal search (e.g. when a state's
automated scraper is IP-blocked, see ucc/oh_playwright.py header) into the
same ucc_filings table + deals pipeline the automated scrapers feed.

Expects a CSV with these columns (see scripts/oh_manual_search_template.py
or any exported worklist for the shape):
    query_name, found_y_n, debtor_name, secured_party, filing_number,
    filing_date, lapse_date, notes
Only rows with found_y_n in {y, yes, 1} AND a non-empty filing_number are
ingested — everything else (not-yet-searched, confirmed-not-found) is
skipped. filing_date/lapse_date accept MM/DD/YYYY (portal's own format) or
YYYY-MM-DD.

Usage:
    venv/bin/python3 scripts/ingest_manual_ucc.py <state> <csv_path>
    venv/bin/python3 scripts/ingest_manual_ucc.py OH ~/oh_manual_search_worklist.csv
"""
import csv
import sys
from datetime import datetime

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
import psycopg2.extras
from config import get_config
from ucc.base import UCCFiling
from scraper.ucc import _filing_to_article
import main as main_mod


def _parse_date(s: str):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    print(f"  warning: couldn't parse date {s!r}, leaving blank")
    return None


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    state = sys.argv[1].upper()
    csv_path = sys.argv[2]

    config = get_config()
    conn = psycopg2.connect(config.database_url)
    psycopg2.extras.register_uuid()

    filings = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            found = (row.get("found_y_n") or "").strip().lower()
            filing_number = (row.get("filing_number") or "").strip()
            if found not in ("y", "yes", "1") or not filing_number:
                continue
            filings.append(UCCFiling(
                state=state,
                debtor_name=(row.get("debtor_name") or row.get("query_name") or "").strip(),
                secured_party_name=(row.get("secured_party") or "").strip(),
                filing_number=filing_number,
                filing_date=_parse_date(row.get("filing_date")),
                lapse_date=_parse_date(row.get("lapse_date")),
                source_url=f"https://ucc.ohiosos.gov/search" if state == "OH" else None,
                raw={"query_name": row.get("query_name"), "notes": row.get("notes"), "manual_entry": True},
                source_confidence="manual",
            ))

    print(f"Ingesting {len(filings)} manually-found {state} filings from {csv_path}")

    ucc_source_id = main_mod.ensure_ucc_source(conn)
    new_deals = 0
    for filing in filings:
        article = _filing_to_article(filing)
        article["source_id"] = ucc_source_id
        new_deals += main_mod.process_article(article, conn)
        conn.commit()

    print(f"Stored {new_deals} new/updated deals from {len(filings)} filings")

    try:
        from scripts.relink_cms_ucc import relink_cms_ucc
        relinked = relink_cms_ucc(conn, states=[state], verbose=True)
        print(f"CMS/UCC relink: {relinked} previously-unconfirmed deals now corroborated")
    except Exception as e:
        print(f"CMS/UCC relink skipped/failed: {e}")

    conn.close()


if __name__ == "__main__":
    main()
