"""
ucc/nj_playwright.py
NJ UCC Non-Certified Search via Playwright.
"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from ucc.base import UCCFiling
from datetime import datetime
import logging
import time

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

def search_nj(owner_name: str, include_lapsed: bool = True) -> list[UCCFiling]:
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
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

            # parse results
            results.extend(_parse_results(page.content(), owner_name))

            browser.close()
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_nj("COMPLETE CARE")
    for r in results:
        print(r.filing_number, "|", r.debtor_name, "|", r.status, "|", r.filing_date)
