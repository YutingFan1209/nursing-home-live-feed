"""
scripts/ny_nyx_backfill.py

Finds the real NY filings behind the June 2026 spreadsheet deals. That
backfill (ucc/ny.py) had no filing numbers, so its deals carry made-up
'NYX-<hash>' keys; scripts/export_deals.py links one to its real filing
when ucc_filings has the same person's filing from the same day. Until
2026-10-15 the NY scraper read only the first page of results (10 filings,
oldest first), so many people's newer filings never reached ucc_filings.

This re-searches the people behind still-unmatched NYX deals (all result
pages now) and saves the filings ucc_filings doesn't have yet. Where a
person has several filings on the deal's date it also fetches each one's
secured party, which the export uses to pick the right one. No deals are
created or changed. Then re-run scripts/export_deals.py.

Usage:
    venv/bin/python3 scripts/ny_nyx_backfill.py [--dry-run]
"""
import sys
import logging
from collections import defaultdict

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
from playwright.sync_api import sync_playwright
from config import get_config
from ucc.ny_playwright import search_ny_batch_cdp, _get_secured_party, CDP_URL
from ucc.audit_log import save_ucc_filings
from scripts.export_deals import _ny_spreadsheet_links, _match_ny_spreadsheet

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("ny_nyx_backfill")


def unmatched_nyx(cur) -> list[tuple[str, str, str]]:
    """(debtor, filing date, lender) for each NYX deal with no real filing."""
    cur.execute("""
        SELECT UPPER(TRIM(SUBSTRING(a.title FROM '^UCC-1 filing: (.+?) / '))),
               a.published_at::date::text, d.lender
        FROM deals d JOIN articles a ON a.id = d.article_id
        WHERE a.url LIKE 'ucc://NY/NYX-%'""")
    rows = cur.fetchall()
    index = _ny_spreadsheet_links(cur)
    return [r for r in rows if r[0] and not _match_ny_spreadsheet(index, *r)]


def portal_name(debtor: str) -> str | None:
    """'FIRST [MIDDLE] LAST' -> 'LAST, FIRST' for the portal's individual
    search (Starts-with logic, so the middle name is left out)."""
    parts = debtor.split()
    return f"{parts[-1]}, {parts[0]}" if len(parts) >= 2 else None


def main(dry_run: bool):
    conn = psycopg2.connect(get_config().database_url)
    cur = conn.cursor()
    deals = unmatched_nyx(cur)
    people = sorted({portal_name(d) for d, _, _ in deals} - {None})
    log.info("%d unmatched NYX deals, %d people to search", len(deals), len(people))
    if dry_run:
        return

    filings = search_ny_batch_cdp(individual_names=people)
    cur.execute("SELECT filing_number FROM ucc_filings WHERE state = 'NY'")
    known = {r[0] for r in cur.fetchall()}
    new = {f.filing_number: f for f in filings if f.filing_number not in known}

    # Several filings by the person on the deal's date: the export needs each
    # one's secured party to tell them apart (the search fetches it only for
    # active filings)
    wanted = {(d, fd) for d, fd, _ in deals}
    same_day = defaultdict(list)
    for f in filings:
        key = (f.debtor_name.upper().strip(), f.filing_date.isoformat() if f.filing_date else "")
        if key in wanted:
            same_day[key].append(f)
    need_sp = [f for fs in same_day.values() if len(fs) > 1 for f in fs
               if not f.secured_party_name and f.raw.get("internal_id")]
    if need_sp:
        log.info("fetching the secured party for %d same-day filings", len(need_sp))
        with sync_playwright() as p:
            page = p.chromium.connect_over_cdp(CDP_URL).contexts[0].new_page()
            for f in need_sp:
                sp = _get_secured_party(page, f.raw["internal_id"])
                if sp:
                    f.secured_party_name, f.raw["sp_address"] = sp["name"], sp.get("address", "")
                    new[f.filing_number] = f
            page.close()

    # Only new filings and ones with a secured party just fetched: re-saving
    # a known filing without its secured party would blank the stored one
    log.info("%d filings found, %d to save", len(filings), len(new))
    save_ucc_filings(list(new.values()), conn)
    left = unmatched_nyx(cur)
    log.info("NYX deals still without a real filing: %d (was %d)", len(left), len(deals))


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
