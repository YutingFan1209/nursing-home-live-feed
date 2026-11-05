"""
Texas nursing home owners from HHSC's TULIP LTC Provider Search.

Texas publishes no sale filing (CHOW applications in TULIP aren't public)
and no licence download, but TULIP's public provider search lists every
nursing facility (Program Type "Nursing Facilities (NF)": 1,281 on
2026-11-05) and each home's page names its "Owner", the licence holder.
There is no licence number or date, so the TX licence list (licence_lists.py)
works like New York's: homes are matched to CMS by address, the first read
is only stored as the baseline, and a later read showing a different owner
is the change.

Many Texas homes are licensed to rural hospital districts (QIPP) and run by
a private company under a management deal (Avir at Azalea Heights: West
Wharton County Hospital District). A sale of that company doesn't change
the owner shown here; CMS's ownership file catches it later.

The pages are a Salesforce site rendered in the browser, so they're read in
headless Chromium: the search's 13 result pages, then each home's page, one
at a time with a pause (about 45 minutes). robots.txt allows everything;
HHSC's site policy allows non-commercial copying with attribution.
licence_lists.py only calls this when the last TX read is over 25 days old.
"""

import logging
import re
import time

logger = logging.getLogger(__name__)

SEARCH_URL = "https://tulip.hhs.texas.gov/TULIP/s/ltc-provider-search"
PAUSE_S = 0.8
_OWNER_RE = re.compile(r"Owner:\s*(.+)")
_DONE_STATUSES = ("CLOSED", "EXPIRED", "REVOKED", "DENIED", "WITHDRAWN", "SURRENDER")


def parse_detail(text: str) -> str | None:
    m = _OWNER_RE.search(text or "")
    return m.group(1).strip() or None if m else None


def parse_result_row(cells: list[str], href: str) -> dict | None:
    """Search table cells: name, status, service type, program, provider
    type, services, address, city, county, zip."""
    cells = [c.strip() for c in cells]
    if len(cells) < 10 or not cells[0]:
        return None
    if any(s in cells[1].upper() for s in _DONE_STATUSES):
        return None
    return {"facility": cells[0], "status": cells[1], "service_type": cells[2],
            "address": cells[6], "city": cells[7], "zip": cells[9], "detail_url": href}


def _list_homes(page) -> list[dict]:
    page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=90000)
    page.get_by_role("combobox", name="Program Type").click()
    page.get_by_role("option", name="Nursing Facilities (NF)").click()
    page.get_by_role("button", name="Search").click()
    page.wait_for_selector("a[href*='ltc-provider-detail']", timeout=90000)
    total = int(re.search(r"Total Records:\s*(\d+)",
                          page.get_by_text(re.compile(r"Total Records:\s*[1-9]")).first.inner_text()).group(1))
    links = page.locator("a[href*='ltc-provider-detail']")
    for _ in range(4):  # the table re-renders after the search; the first pick can be lost
        page.locator("select[id^='recordsPerPage']").select_option("100")
        page.wait_for_timeout(4000)
        if links.count() > 10:
            break
    else:
        raise RuntimeError("TULIP page size didn't change to 100")
    homes, seen_pages = {}, 0
    while True:
        seen_pages += 1
        first = page.locator("a[href*='ltc-provider-detail']").first.get_attribute("href")
        for tr in page.locator("tbody tr").all():
            link = tr.locator("a[href*='ltc-provider-detail']")
            if not link.count():
                continue
            cells = [c.inner_text() for c in tr.locator("th, td").all()]
            row = parse_result_row(cells, link.first.get_attribute("href"))
            if row:
                homes[row["detail_url"]] = row
        nxt = page.get_by_role("button", name="Next")
        if seen_pages * 100 >= total or not nxt.count() or nxt.first.is_disabled():
            break
        nxt.first.click()
        page.wait_for_function(
            "f => document.querySelector(\"a[href*='ltc-provider-detail']\")?.getAttribute('href') !== f",
            arg=first, timeout=60000)
        time.sleep(PAUSE_S)
    logger.info(f"TX TULIP: {total} nursing facilities listed, {len(homes)} kept from {seen_pages} pages")
    return list(homes.values())


def fetch_tx_rows() -> list[dict]:
    """-> licence_lists rows (no CCN, no date): licensee = TULIP's Owner."""
    from playwright.sync_api import sync_playwright
    rows, missing = [], 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            homes = _list_homes(page)
            for i, home in enumerate(homes, 1):
                owner = None
                for attempt in range(2):
                    try:
                        page.goto(home["detail_url"], wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_selector("text=Owner:", timeout=30000)
                        owner = parse_detail(page.inner_text("body"))
                        break
                    except Exception as e:
                        if attempt:
                            logger.debug(f"TX TULIP: no owner for {home['facility']}: {e}")
                        time.sleep(5)
                if owner:
                    rows.append({"ccn": None, "licensee": owner, "licence_date": None,
                                 "facility": home["facility"], "city": home["city"],
                                 "address": home["address"]})
                else:
                    missing += 1
                if i % 200 == 0:
                    logger.info(f"TX TULIP: {i}/{len(homes)} home pages read")
                time.sleep(PAUSE_S)
        finally:
            browser.close()
    if missing:
        logger.info(f"TX TULIP: {missing} home pages gave no owner")
    return rows
