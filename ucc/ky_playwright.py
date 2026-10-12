"""
ucc/ky_playwright.py
Kentucky UCC search via Playwright (web.sos.ky.gov, standard ASP.NET WebForms, no Cloudflare).
"""
from __future__ import annotations
import logging
from datetime import datetime, date
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from ucc.base import UCCFiling
from pipeline.run_health import health

logger = logging.getLogger(__name__)
BASE_URL = "https://web.sos.ky.gov/ftucc/search.aspx"

def _parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y %I:%M %p").date()
    except Exception:
        return None

def _parse_individual_name(name: str):
    """CMS lists people as 'LAST, FIRST [MIDDLE]'; KY's form has separate
    last and first name boxes for individual debtors."""
    if "," not in (name or ""):
        return None
    last, first = (p.strip() for p in name.split(",", 1))
    first = first.split()[0] if first else ""
    return (last, first) if last and first else None


def _search_one(page, owner_name: str, is_individual: bool = False) -> list[UCCFiling]:
    results = []
    try:
        page.goto(BASE_URL)
        page.wait_for_timeout(1500)
        person = _parse_individual_name(owner_name) if is_individual else None
        if person:
            page.fill("#ctl00_ContentPlaceHolder1_SearchForm1_tIlname", person[0])
            page.fill("#ctl00_ContentPlaceHolder1_SearchForm1_tIfname", person[1])
        else:
            page.fill("#ctl00_ContentPlaceHolder1_SearchForm1_tOrgname", owner_name)
        page.click("#ctl00_ContentPlaceHolder1_SearchForm1_bSearch")
        page.wait_for_load_state("networkidle", timeout=10000)
        page.wait_for_timeout(1000)

        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        tables = soup.find_all("table")
        if len(tables) < 2:
            logger.info("KY UCC %s → 0 filings", owner_name)
            return []

        result_table = tables[1]
        rows = result_table.find_all("tr")[1:]  # skip header

        today = date.today()
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 5:
                continue
            debtor_name, file_num, file_date_str, lapse_date_str, secured_party = cells[:5]
            secured_party = secured_party.replace("*", "").strip()
            lapse_date = _parse_date(lapse_date_str)
            filing_date = _parse_date(file_date_str)
            status = "active" if lapse_date and lapse_date > today else "lapsed"

            # Each result row links to search.aspx?filing={id} -- a real,
            # no-auth-needed per-filing detail page (confirmed 2026-09-16).
            # Capture it the same way ny_playwright.py captures NY's lienId.
            link = row.find("a", href=True)
            internal_id = None
            if link and "filing=" in link["href"]:
                internal_id = link["href"].split("filing=", 1)[1].split("&", 1)[0]

            results.append(UCCFiling(
                state="KY",
                debtor_name=debtor_name,
                secured_party_name=secured_party,
                filing_number=file_num,
                filing_date=filing_date,
                lapse_date=lapse_date,
                filing_type="UCC1",
                status=status,
                source_url=BASE_URL,
                raw={"query_name": owner_name, "internal_id": internal_id,
                     "search_mode": "individual" if person else "organization"},
            ))
        logger.info("KY UCC %s → %d filings", owner_name, len(results))
    except Exception as e:
        logger.error("KY search failed for %r: %s", owner_name, e)
        health.failed("UCC KY searches", f"{owner_name}: {e}")
    return results

def _search_chunk(names: list) -> list[UCCFiling]:
    """One worker's share of names, run serially against its own headless
    browser instance (KY has no Cloudflare/fingerprint requirement, so
    unlike NY there's no need to share a single real browser -- each
    thread just gets its own headless Chromium)."""
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for name, is_individual in names:
            results.extend(_search_one(page, name, is_individual))
        browser.close()
    return results


def search_ky_batch(owner_names: list[str], max_workers: int = 4, individual_names: list[str] = None) -> list[UCCFiling]:
    """Runs max_workers headless browser instances in parallel, each
    working through its own slice of owner_names. Kept conservative
    (default 4) -- KY has a confirmed same-day rate limit under sustained
    volume (a second large burst within a few hours of a first got
    TLS-reset-blocked), and concurrency effectively increases request
    rate the same way a bigger burst would, so don't push this much
    higher without testing on a day when re-blocking is low-stakes."""
    terms = [(n, False) for n in owner_names or []] + [(n, True) for n in individual_names or []]
    if not terms:
        return []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    chunks = [c for c in (terms[i::max_workers] for i in range(max_workers)) if c]
    all_results = []
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [executor.submit(_search_chunk, chunk) for chunk in chunks]
        for future in as_completed(futures):
            all_results.extend(future.result())
    return all_results

def search_ky(owner_name: str) -> list[UCCFiling]:
    return search_ky_batch([owner_name])

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_ky_batch(["WOODLAND OAKS OPERATIONS"])
    for r in results:
        print(r.filing_number, "|", r.debtor_name[:35], "|", r.secured_party_name[:30], "|", r.status)
