"""
ucc/ny_playwright.py
NY UCC search via Playwright (Cenuity Online portal).
Uses ucc/base.py UCCFiling for compatibility with existing pipeline.
"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from ucc.base import UCCFiling
from datetime import datetime
import logging
import time

logger = logging.getLogger(__name__)

SEARCH_URL = "https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineUCCSearch"

def _parse_date(s: str):
    """Parse NY portal date string e.g. '6/12/2017 12:00:00 AM'"""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y %I:%M:%S %p").date()
    except Exception:
        return None

def _get_secured_party(page, internal_id: str) -> dict:
    """Navigate to filing detail and extract secured party info."""
    try:
        page.evaluate(f"NavigateLienInfo({internal_id})")
        page.wait_for_timeout(3000)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            if "Secured Party Name" in headers:
                rows = table.select("tbody tr")
                for row in rows:
                    cells = [td.get_text(strip=True) for td in row.find_all("td")]
                    if cells:
                        return {
                            "name": cells[0],
                            "address": cells[1] if len(cells) > 1 else "",
                            "type": cells[2] if len(cells) > 2 else "",
                        }
    except Exception as e:
        logger.warning("Secured party fetch failed for ID %s: %s", internal_id, e)
    return {}

def _return_to_results(page, owner_name: str):
    """Navigate back to search results for owner_name."""
    page.goto(SEARCH_URL)
    page.wait_for_timeout(1500)
    page.click("input[value='DebtorName']")
    page.wait_for_timeout(300)
    page.click("#rdbOrg")
    page.wait_for_timeout(500)
    page.locator("input[name*='OrgName']").first.fill(owner_name)
    page.click("#UCCSearch_UCCSearch_btnSearch")
    page.wait_for_timeout(5000)

def search_ny(owner_name: str, fetch_secured_party: bool = True) -> list[UCCFiling]:
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(SEARCH_URL)
            page.wait_for_timeout(2000)
            page.click("input[value='DebtorName']")
            page.wait_for_timeout(500)
            page.click("#rdbOrg")
            page.wait_for_timeout(1000)
            page.locator("input[name*='OrgName']").first.fill(owner_name)
            page.click("#UCCSearch_UCCSearch_btnSearch")
            page.wait_for_timeout(5000)

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("tbody tr")

            for row in rows:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                hdn = row.find("input", {"id": "hdnIFS"})
                internal_id = hdn["value"] if hdn else ""
                if len(cells) < 8:
                    continue

                # fetch secured party from detail page
                sp = {}
                if fetch_secured_party and internal_id:
                    sp = _get_secured_party(page, internal_id)
                    _return_to_results(page, owner_name)

                status_raw = cells[8] if len(cells) > 8 else ""
                status = status_raw.lower() if status_raw else "unknown"

                results.append(UCCFiling(
                    state="NY",
                    debtor_name=cells[3],
                    secured_party_name=sp.get("name", ""),
                    filing_number=cells[0],
                    filing_date=_parse_date(cells[6]),
                    lapse_date=_parse_date(cells[7]),
                    filing_type=cells[2] or "UCC-1",
                    status=status,
                    source_url=SEARCH_URL,
                    raw={
                        "address": cells[4],
                        "sp_address": sp.get("address", ""),
                        "query_name": owner_name,
                        "internal_id": internal_id,
                    },
                ))
                time.sleep(0.5)

            browser.close()
    except Exception as e:
        logger.error("NY search failed for %r: %s", owner_name, e)
    return results


def save_ucc_filings(filings: list, conn) -> int:
    """Save UCCFiling objects to ucc_filings table. Returns inserted count."""
    from psycopg2.extras import execute_values
    if not filings:
        return 0

    rows = [(
        f.state,
        f.filing_number,
        f.debtor_name,
        f.secured_party_name,
        f.filing_date.isoformat() if f.filing_date else None,
        f.filing_type,
        f.collateral_description or "",
        f.status,
        f.raw.get("query_name", ""),
        f.raw.get("sp_address", ""),
    ) for f in filings]

    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO ucc_filings
                (state, filing_number, debtor_name, secured_party,
                 filing_date, filing_type, collateral_description,
                 status, query_name, sp_address)
            VALUES %s
            ON CONFLICT (state, filing_number) DO NOTHING
        """, rows)
    conn.commit()
    return len(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_ny("BVRNC OPERATING LLC")
    for r in results:
        print(r.filing_number, "|", r.debtor_name, "|", r.secured_party_name, "|", r.status, "|", r.filing_date)
