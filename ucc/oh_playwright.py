"""
ucc/oh_playwright.py
Ohio UCC search via Playwright (Angular Material portal).
Secured party is in search results — no detail page needed.

UNTESTED CDP variant added 2026-09-15: OH's login API started returning
Cloudflare-mitigated challenges (`cf-mitigated: challenge`, `server:
cloudflare`) partway through today, escalated from a plain app-level 429
earlier the same day. This is the same signature NY's Turnstile challenge
had, and the same fix worked there -- a real, independently-launched
Chrome reached over CDP passes it where a Playwright-launched Chromium
(even non-headless, even with the anti-detection args below) doesn't.
search_oh_batch_cdp() mirrors ny_playwright.py's approach but has NOT
been verified against OH yet -- test with a single-name probe before
trusting a big batch, same as any other day's first OH check. If OH's
challenge was actually just a transient rate-limit-window thing that
clears on its own overnight, the plain headless search_oh_batch may
already work fine again -- probe with that first since it's simpler.
"""
from __future__ import annotations
import logging
from datetime import datetime, date
from playwright.sync_api import sync_playwright
from ucc.base import UCCFiling
from ucc.chrome_cdp import CDP_URL, ensure_chrome_cdp

logger = logging.getLogger(__name__)
BASE_URL = "https://ucc.ohiosos.gov"

BROWSER_ARGS = ["--disable-blink-features=AutomationControlled"]
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"

def _parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date()
    except Exception:
        return None

def _parse_individual_name(name: str):
    """CMS individual owner names come as 'LAST, FIRST' -- split for the
    portal's separate First/Last Name fields (personInd1 mode). Returns
    None if the name doesn't have that shape (caller falls back to org
    search for it)."""
    if "," not in name:
        return None
    last, _, first = name.partition(",")
    last, first = last.strip(), first.strip()
    if not last or not first:
        return None
    return last, first

def _search_one(page, owner_name: str, is_individual: bool = False) -> list[UCCFiling]:
    """is_individual routes to the portal's Individual debtor mode
    (personInd1, separate First/Middle/Last Name fields) instead of
    Organization (personInd2, single Organization's Name field) -- the
    portal indexes these separately, same as the NY portal."""
    results = []
    try:
        page.goto(BASE_URL + "/search")
        page.wait_for_selector("button.rs-submit", timeout=15000)
        page.wait_for_timeout(2000)

        # Click Debtor tab
        page.locator("[role='tab'], md-tab-item, .md-tab").nth(1).click()
        page.wait_for_timeout(1000)

        parsed = _parse_individual_name(owner_name) if is_individual else None
        if parsed:
            last, first = parsed
            # Select Individual radio
            page.evaluate("document.getElementById('personInd1').click()")
            page.wait_for_timeout(300)
            page.get_by_placeholder("First Name").fill(first)
            page.get_by_placeholder("Last Name").fill(last)
        else:
            # Select Organization radio
            page.evaluate("document.getElementById('personInd2').click()")
            page.wait_for_timeout(300)

            # Fill name
            page.evaluate(f"""
                var el = document.getElementById('md-input-4');
                el.value = {repr(owner_name)};
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            """)
        page.wait_for_timeout(300)

        # Click Search
        page.click("button.rs-submit")

        # Wait for results
        try:
            page.wait_for_selector("mat-row", timeout=8000)
        except Exception:
            logger.info("OH UCC %s → 0 filings", owner_name)
            return []

        page.wait_for_timeout(500)

        # Extract via JS
        rows = page.evaluate("""
            Array.from(document.querySelectorAll('mat-row')).map(row =>
                Array.from(row.querySelectorAll('mat-cell')).map(cell => cell.innerText.trim())
            )
        """)

        today = date.today()
        for cells in rows:
            if len(cells) < 6:
                continue
            filing_number = cells[0]
            debtor_name = cells[1].replace("\n\n", ", ")
            secured_party = cells[2]
            filing_type = cells[3]
            filing_date = _parse_date(cells[4])
            lapse_date = _parse_date(cells[5])
            status = "active" if lapse_date and lapse_date > today else "lapsed"

            results.append(UCCFiling(
                state="OH",
                debtor_name=debtor_name,
                secured_party_name=secured_party,
                filing_number=filing_number,
                filing_date=filing_date,
                lapse_date=lapse_date,
                filing_type=filing_type,
                status=status,
                source_url=BASE_URL + "/search",
                raw={"query_name": owner_name, "search_mode": "individual" if parsed else "organization"},
            ))
        logger.info("OH UCC %s (%s) → %d filings", owner_name, "individual" if parsed else "org", len(results))
    except Exception as e:
        logger.error("OH search failed for %r: %s", owner_name, e)
    return results


def _search_chunk(terms: list[tuple[str, bool]]) -> list[UCCFiling]:
    """One worker's share of (name, is_individual) terms, each run in its
    own hidden-window browser instance (OH needs headless=False -- a
    plain headless launch gets flagged -- but doesn't need a real
    external Chrome/CDP like NY does, so each thread can just launch its
    own Playwright-bundled Chromium)."""
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=BROWSER_ARGS + ["--window-position=-10000,-10000"])
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        for name, is_individual in terms:
            results.extend(_search_one(page, name, is_individual=is_individual))
        browser.close()
    return results


def search_oh_batch(org_names: list[str] = None, individual_names: list[str] = None, max_workers: int = 3) -> list[UCCFiling]:
    """Search organization and individual debtor names. Individual names
    are routed to the portal's Individual debtor mode (personInd1) --
    see _search_one. Runs max_workers hidden-window browser instances in
    parallel, each working through its own slice of terms. Kept more
    conservative than KY/NY's default of 4 (default 3 here) -- OH hit a
    live 429 rate limit under repeated same-day volume on 2026-09-15
    (since cleared), the most fragile of the three automated states, and
    concurrency effectively raises request rate the same way more volume
    would."""
    terms = [(n, False) for n in (org_names or [])] + [(n, True) for n in (individual_names or [])]
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


def search_oh(owner_name: str, is_individual: bool = False) -> list[UCCFiling]:
    if is_individual:
        return search_oh_batch(individual_names=[owner_name])
    return search_oh_batch(org_names=[owner_name])


def _search_chunk_cdp(cdp_url: str, terms: list[tuple[str, bool]]) -> list[UCCFiling]:
    results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        for name, is_individual in terms:
            filings = _search_one(page, name, is_individual=is_individual)
            logger.info("OH UCC %s (%s, cdp) → %d filings", name, "individual" if is_individual else "org", len(filings))
            results.extend(filings)
        page.close()
    return results


def search_oh_batch_cdp(
    org_names: list[str] = None,
    individual_names: list[str] = None,
    cdp_url: str = CDP_URL,
    max_workers: int = 2,
) -> list[UCCFiling]:
    """UNTESTED as of 2026-09-15 -- see module docstring. Mirrors
    ny_playwright.py:search_ny_batch_cdp: drives a real, independently-
    launched Chrome over CDP (auto-launched if not already running,
    shared with NY's instance) instead of a Playwright-launched Chromium.
    Try search_oh_batch (the plain, already-working path) with a
    single-name probe first -- only reach for this if that still shows
    the Cloudflare-mitigated signature. max_workers defaults lower (2)
    than search_oh_batch's 3 since this is unverified against OH; probe
    with max_workers=1 before trusting any concurrency here at all.
    """
    ensure_chrome_cdp(cdp_url)
    terms = [(n, False) for n in (org_names or [])] + [(n, True) for n in (individual_names or [])]
    if not terms:
        return []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    chunks = [c for c in (terms[i::max_workers] for i in range(max_workers)) if c]
    logger.info("OH UCC (cdp): %d terms split across %d workers", len(terms), len(chunks))
    all_results = []
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [executor.submit(_search_chunk_cdp, cdp_url, chunk) for chunk in chunks]
        for future in as_completed(futures):
            all_results.extend(future.result())
    return all_results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_oh_batch(org_names=["OHIO LIVING", "TRILOGY HEALTH SERVICES"])
    for r in results:
        print(r.filing_number, "|", r.debtor_name[:40], "|", r.secured_party_name[:30], "|", r.status)
