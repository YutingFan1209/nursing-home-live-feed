"""
ucc/ny_playwright.py
NY UCC search via Playwright (Cenuity Online portal).
"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from ucc.query import UCCFiling
import logging
import time

logger = logging.getLogger(__name__)

SEARCH_URL = "https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineUCCSearch"

def _get_secured_party(page, internal_id: str) -> dict:
    """Click into filing detail and extract secured party info."""
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

                # fetch secured party
                sp = {}
                if fetch_secured_party and internal_id:
                    sp = _get_secured_party(page, internal_id)
                    # go back to results
                    page.goto(SEARCH_URL)
                    page.wait_for_timeout(1000)
                    page.click("input[value='DebtorName']")
                    page.wait_for_timeout(300)
                    page.click("#rdbOrg")
                    page.wait_for_timeout(500)
                    page.locator("input[name*='OrgName']").first.fill(owner_name)
                    page.click("#UCCSearch_UCCSearch_btnSearch")
                    page.wait_for_timeout(5000)

                results.append(UCCFiling(
                    state="NY",
                    debtor_name=cells[3],
                    secured_party=sp.get("name", ""),
                    filing_number=cells[0],
                    filing_date=cells[6],
                    filing_type=cells[2],
                    collateral_description="",
                    status=cells[8] if len(cells) > 8 else "Unknown",
                    query_name=owner_name,
                    source_url=SEARCH_URL,
                    raw={
                        "address": cells[4],
                        "lapse_date": cells[7],
                        "sp_address": sp.get("address", ""),
                    },
                ))
                time.sleep(1)

            browser.close()
    except Exception as e:
        logger.error("NY search failed for %r: %s", owner_name, e)
    return results

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_ny("BVRNC OPERATING LLC")
    for r in results:
        print(r.filing_number, "|", r.debtor_name, "|", r.secured_party, "|", r.status)
