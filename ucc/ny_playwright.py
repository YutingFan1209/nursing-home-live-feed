"""
ucc/ny_playwright.py
NY UCC search via Playwright (Cenuity Online portal).
Optimizations: reuse browser session, only fetch secured party for Active filings.
"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from ucc.base import UCCFiling
from datetime import datetime
import logging
import time

logger = logging.getLogger(__name__)

SEARCH_URL = "https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineUCCSearch"

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
]

def _parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y %I:%M:%S %p").date()
    except Exception:
        return None

def _get_secured_party(page, internal_id: str) -> dict:
    try:
        url = f"https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineLienInformation?lienId={internal_id}"
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(500)
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
                        }
    except Exception as e:
        logger.warning("Secured party fetch failed for ID %s: %s", internal_id, e)
    return {}

def _return_to_results(page, owner_name: str):
    page.goto(SEARCH_URL)
    page.wait_for_timeout(1500)
    page.click("input[value='DebtorName']")
    page.wait_for_timeout(300)
    page.click("#rdbOrg")
    page.wait_for_timeout(500)
    page.locator("input[name*='OrgName']").first.fill(owner_name)
    page.click("#UCCSearch_UCCSearch_btnSearch")
    page.wait_for_timeout(4000)

def _search_one(page, owner_name: str) -> list[UCCFiling]:
    """Search for one owner name using an existing page object."""
    results = []
    try:
        page.goto(SEARCH_URL)
        page.wait_for_timeout(1500)
        page.click("input[value='DebtorName']")
        page.wait_for_timeout(300)
        page.click("#rdbOrg")
        page.wait_for_timeout(500)
        page.locator("input[name*='OrgName']").first.fill(owner_name)
        page.click("#UCCSearch_UCCSearch_btnSearch")
        page.wait_for_timeout(4000)

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("tbody tr")

        # First pass: collect all rows without navigating away
        parsed_rows = []
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            hdn = row.find("input", {"id": "hdnIFS"})
            internal_id = hdn["value"] if hdn else ""
            if len(cells) < 8:
                continue
            status_raw = cells[8] if len(cells) > 8 else ""
            status = status_raw.lower() if status_raw else "unknown"
            parsed_rows.append((cells, internal_id, status))

        # Second pass: fetch secured party for Active filings only
        sp_cache = {}
        active_ids = [iid for _, iid, st in parsed_rows if st == "active" and iid]
        for internal_id in active_ids:
            sp_cache[internal_id] = _get_secured_party(page, internal_id)

        # Build UCCFiling objects
        for cells, internal_id, status in parsed_rows:
            sp = sp_cache.get(internal_id, {})
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
    except Exception as e:
        logger.error("NY search failed for %r: %s", owner_name, e)
    return results


def search_ny(owner_name: str) -> list[UCCFiling]:
    """Search a single owner name. Opens/closes its own browser."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
        page = browser.new_page()
        results = _search_one(page, owner_name)
        browser.close()
    return results


def search_ny_batch(owner_names: list[str], max_workers: int = 4) -> list[UCCFiling]:
    """Search multiple owner names in parallel using multiple browser instances."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    all_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(search_ny, name): name for name in owner_names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                filings = future.result()
                logger.info("NY UCC %s → %d filings", name, len(filings))
                all_results.extend(filings)
            except Exception as e:
                logger.warning("NY UCC failed for %r: %s", name, e)
    return all_results


def save_ucc_filings(filings: list, conn) -> int:
    from psycopg2.extras import execute_values
    from ucc.lender_classifier import classify_secured_party, to_confidence_label
    if not filings:
        return 0
    rows = [(
        f.state, f.filing_number, f.debtor_name, f.secured_party_name if f.secured_party_name else None,
        f.filing_date.isoformat() if f.filing_date else None,
        f.filing_type, f.collateral_description or "", f.status,
        f.raw.get("query_name", ""), f.raw.get("sp_address", ""),
        to_confidence_label(classify_secured_party(f.secured_party_name)) if f.secured_party_name else None,
    ) for f in filings]
    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO ucc_filings
                (state, filing_number, debtor_name, secured_party,
                 filing_date, filing_type, collateral_description,
                 status, query_name, sp_address, confidence)
            VALUES %s
            ON CONFLICT (state, filing_number) DO UPDATE
            SET confidence = EXCLUDED.confidence,
                secured_party = EXCLUDED.secured_party
        """, rows)
    conn.commit()
    return len(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_ny_batch(["BVRNC OPERATING LLC", "CORTLAND ACQUISITION LLC"])
    for r in results:
        print(r.filing_number, "|", r.debtor_name, "|", r.secured_party_name, "|", r.status)
