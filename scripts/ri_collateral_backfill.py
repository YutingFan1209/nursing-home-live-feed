"""
Backfill collateral text for Rhode Island UCC filings stored before
ucc/ri.py kept it (2026-10-28).

The filing history page needs the portal's internal UCC1 id, which wasn't
stored, so each lender's results page is fetched again to find it, then each
filing's history page. 5 s between requests, as in ucc/ri.py. Only fills
filings whose collateral_text is empty, so a rerun picks up where one stopped.

    venv/bin/python3 scripts/ri_collateral_backfill.py
"""
import logging
import sys
import time

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")

import psycopg2
import requests

from config import get_config
from ucc import ri

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def main():
    conn = psycopg2.connect(get_config().database_url)
    with conn.cursor() as cur:
        cur.execute("""SELECT query_name, array_agg(filing_number) FROM ucc_filings
                       WHERE state = 'RI' AND COALESCE(collateral_text, '') = ''
                       GROUP BY 1 ORDER BY 1""")
        todo = cur.fetchall()
    session, filled, missing, first = requests.Session(), 0, [], True
    for lender, numbers in todo:
        if not first:
            time.sleep(ri.GAP)
        first = False
        url = ri.results_url(lender)
        r = session.get(url, headers=ri.HEADERS, timeout=90)
        r.raise_for_status()
        ids = {row["filing_number"]: row["ucc1"] for row in ri.parse_results(r.text)
               if row["filing_type"].upper().startswith("UCC-1")}
        for number in numbers:
            if number not in ids:
                missing.append(number)
                continue
            time.sleep(ri.GAP)
            h = session.get(ri.BASE + f"UCCFilingHistory.aspx?SearchLapsed=False&UCC1={ids[number]}",
                            headers={**ri.HEADERS, "Referer": url}, timeout=90)
            h.raise_for_status()
            text = ri.parse_history(h.text).get("collateral")
            if not text:
                missing.append(number)
                continue
            with conn.cursor() as cur:
                cur.execute("""UPDATE ucc_filings SET collateral_text = %s, collateral_source = 'page_text'
                               WHERE state = 'RI' AND filing_number = %s""", (text, number))
            conn.commit()
            filled += 1
        log.info(f"{lender}: {len(numbers)} filings, {filled} filled so far")
    log.info(f"Done: {filled} filled, {len(missing)} without collateral text: {missing}")


if __name__ == "__main__":
    main()
