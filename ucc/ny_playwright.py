from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from ucc.query import UCCFiling
import logging

logger = logging.getLogger(__name__)

SEARCH_URL = "https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineUCCSearch"

def search_ny(owner_name: str) -> list[UCCFiling]:
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

            page.locator("input[name*='OrgName'], input[id*='OrgName']").first.fill(owner_name)
            page.click("#UCCSearch_UCCSearch_btnSearch")
            page.wait_for_timeout(5000)

            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("tbody tr")
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 8:
                continue
            results.append(UCCFiling(
                state="NY",
                debtor_name=cells[3],
                secured_party="",
                filing_number=cells[0],
                filing_date=cells[6],
                filing_type=cells[2],
                collateral_description="",
                status=cells[8] if len(cells) > 8 else "Unknown",
                query_name=owner_name,
                source_url=SEARCH_URL,
                raw={"address": cells[4], "debtor_type": cells[5], "lapse_date": cells[7]},
            ))
    except Exception as e:
        logger.error("NY Playwright search failed for %r: %s", owner_name, e)
    return results

if __name__ == "__main__":
    results = search_ny("Ensign")
    for r in results:
        print(r.filing_number, "|", r.debtor_name, "|", r.status)
