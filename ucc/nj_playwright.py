"""
ucc/nj_playwright.py
NJ UCC Non-Certified Search via Playwright.

No Cloudflare/Incapsula-style bot detection observed as of 2026-09-22 (a
141-name sequential run completed cleanly) -- so unlike NY/PA/CA this
doesn't need a shared real Chrome over CDP, each worker just gets its own
plain headless Chromium, same pattern as ucc/ky_playwright.py. Concurrency
itself hasn't been volume-tested though (only ever run sequentially before
today) -- probe with a small batch before trusting max_workers at scale,
same caution as every other state in this module.
"""
from __future__ import annotations
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from ucc.base import UCCFiling
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.njportal.com/ucc/search/noncertifiedsearch.aspx"

def _parse_date(s: str):
    if not s:
        return None
    for fmt in ["%m/%d/%Y", "%m/%d/%Y %I:%M:%S %p"]:
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except Exception:
            continue
    return None


def _search_one(page, owner_name: str, include_lapsed: bool = True) -> list[UCCFiling]:
    """Runs the full wizard flow (goto -> Organization+StatusReport radios
    -> Continue -> fill name -> Search) on the given page. Called once per
    name -- the wizard has no tested "search again" shortcut, so each name
    gets a fresh navigation rather than risking stale wizard state."""
    results = []
    try:
        page.goto(SEARCH_URL)
        page.wait_for_timeout(2000)

        # Step 1: select Organization + Status Report
        page.click("#ctl00_mainContent_DebtorSearch1_Wizard1_radioSwitchOrgPerson_1")
        page.wait_for_timeout(500)
        page.click("#ctl00_mainContent_DebtorSearch1_Wizard1_radioOutputList_0")
        page.wait_for_timeout(500)
        page.click("input[value='Continue'], button:has-text('Continue')")
        page.wait_for_timeout(2000)

        # Step 2: fill name
        if include_lapsed:
            page.check("input[type='checkbox']")
            page.wait_for_timeout(300)
        page.fill("#ctl00_mainContent_DebtorSearch1_Wizard1_txtOrganizationName", owner_name)
        page.click("input[value='Search'], button:has-text('Search')")
        page.wait_for_timeout(4000)

        results.extend(_parse_results(page.content(), owner_name))
        logger.info("NJ UCC %s -> %d filings", owner_name, len(results))
    except Exception as e:
        logger.error("NJ search failed for %r: %s", owner_name, e)
    return results


def _parse_results(html: str, owner_name: str) -> list[UCCFiling]:
    filings = []
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if "Filing Number" not in headers:
            continue
        for row in table.select("tbody tr"):
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 6:
                continue
            # cells: [checkbox, org_name, city, filing_number, status, filing_date_short, filing_date_long, page_count]
            org_name = cells[1]
            city = cells[2]
            filing_number = cells[3]
            status = cells[4].lower()
            filing_date = _parse_date(cells[5])
            if not filing_number or not org_name or not filing_number.isdigit() or len(filing_number) < 6:
                continue
            filings.append(UCCFiling(
                state="NJ",
                debtor_name=org_name,
                secured_party_name="",  # not in list view, needs per-filing fetch
                filing_number=filing_number,
                filing_date=filing_date,
                status=status,
                source_url=SEARCH_URL,
                raw={
                    "city": city,
                    "query_name": owner_name,
                },
            ))
        break
    return filings


def _search_chunk(names: list[str], include_lapsed: bool = True) -> list[UCCFiling]:
    """One worker's share of names, run serially against its own headless
    browser instance -- a fresh page per name (not a fresh browser), so
    the browser-launch cost is paid once per worker instead of once per
    name."""
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for name in names:
            page = browser.new_page()
            try:
                results.extend(_search_one(page, name, include_lapsed))
            finally:
                page.close()
        browser.close()
    return results


def search_nj_batch(owner_names: list[str], max_workers: int = 8, include_lapsed: bool = True) -> list[UCCFiling]:
    """Runs max_workers headless browser instances in parallel, each
    working through its own slice of owner_names -- same pattern as
    ucc/ky_playwright.py:search_ky_batch. Probed clean at both 4 and 8
    workers on 2026-09-22 (8 names and 16 names respectively, no errors,
    no signs of blocking, results matched a prior sequential run
    exactly) -- only tested up to 16 names total though, not the full
    ~141-name NJ list, so treat 8 as reasonable-but-not-proven at real
    volume rather than fully validated."""
    if not owner_names:
        return []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    chunks = [c for c in (owner_names[i::max_workers] for i in range(max_workers)) if c]
    all_results = []
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [executor.submit(_search_chunk, chunk, include_lapsed) for chunk in chunks]
        for future in as_completed(futures):
            all_results.extend(future.result())
    return all_results


def search_nj(owner_name: str, include_lapsed: bool = True) -> list[UCCFiling]:
    return search_nj_batch([owner_name], max_workers=1, include_lapsed=include_lapsed)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_nj("COMPLETE CARE")
    for r in results:
        print(r.filing_number, "|", r.debtor_name, "|", r.status, "|", r.filing_date)
