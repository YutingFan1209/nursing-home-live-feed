"""
ucc/ny_playwright.py
NY UCC search via Playwright (Cenuity Online portal).
Optimizations: reuse browser session, only fetch secured party for Active filings.

As of ~2026-09, the portal added a Cloudflare Turnstile challenge that
headless Playwright never gets past (page never renders the real form --
see search_ny_batch's docstring). CONFIRMED FIX (2026-09-15): a real,
non-headless Chrome reached via CDP (connect_over_cdp) passes the
Turnstile challenge fine, same technique as pa_playwright.py/
ca_playwright.py. The remaining wrinkle specific to NY: a plain
page.click() on the Search button silently does nothing (something about
the Turnstile widget/overlay swallows the synthetic click) -- calling the
page's own `uccSearchVM.SearchButtonClick()` handler via page.evaluate()
works reliably instead. See search_ny_batch_cdp().
"""
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from ucc.base import UCCFiling
from ucc.chrome_cdp import CDP_URL, ensure_chrome_cdp, page_target_id, run_tab_workers
from datetime import datetime
import logging
import re
import time
from pipeline.run_health import health

logger = logging.getLogger(__name__)

SEARCH_URL = "https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineUCCSearch"
# Result pages read per search (10 filings each), so a broad org name that
# matches hundreds of filings can't tie up a tab
MAX_PAGES = 10


def _ensure_chrome_cdp(cdp_url: str = CDP_URL) -> None:
    ensure_chrome_cdp(cdp_url)

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

def _parse_individual_name(name: str):
    """CMS individual owner names come as 'LAST, FIRST' — split for the
    portal's separate Last/First Name fields. Returns None if the name
    doesn't have that shape (caller should fall back to org search)."""
    if "," not in name:
        return None
    last, _, first = name.partition(",")
    last, first = last.strip(), first.strip()
    if not last or not first:
        return None
    return last, first

def _wait_for_records(page, first: int, timeout_ms: int = 15000) -> None:
    """Wait until the results show "records {first} to ..." or, for an
    empty search, the portal's "No records found" alert."""
    try:
        page.wait_for_function(
            "first => new RegExp('records ' + first + ' to \\\\d+ of|No records found').test(document.body.innerText)",
            arg=first, timeout=timeout_ms)
    except Exception:
        pass


def _search_one(page, search_term: str, is_individual: bool = False) -> list[UCCFiling]:
    """Search for one owner name using an existing page object.
    is_individual routes to the portal's Individual debtor search (separate
    Last/First Name fields) instead of Organization Name — the two modes
    query different indexes on this portal, so using the wrong one for a
    person's name returns near-zero real matches."""
    results = []
    try:
        page.goto(SEARCH_URL)
        page.wait_for_timeout(1500)
        page.click("input[value='DebtorName']")
        page.wait_for_timeout(300)

        parsed = _parse_individual_name(search_term) if is_individual else None
        if parsed:
            last, first = parsed
            page.click("#rdbIndividual")
            page.wait_for_timeout(500)
            page.locator("input[id*='LastName']").first.fill(last)
            page.locator("input[id*='FirstName']").first.fill(first)
        else:
            page.click("#rdbOrg")
            page.wait_for_timeout(500)
            page.locator("input[name*='OrgName']").first.fill(search_term)

        # Give the Cloudflare Turnstile widget time to auto-validate (only
        # relevant on a real/CDP-connected browser -- headless never gets
        # this far, it times out above). A plain page.click() on the Search
        # button silently no-ops here; calling the page's own click handler
        # directly is what actually submits the form. See module docstring.
        page.wait_for_timeout(2000)
        page.evaluate("() => uccSearchVM.SearchButtonClick()")
        page.wait_for_timeout(4000)

        # First pass: collect all rows without navigating away. Results come
        # 10 to a page, oldest first, so a name with more than 10 filings has
        # its newest ones on later pages -- reading only page 1 missed them
        # (found 2026-10-15). The portal's own Next link calls $xhtml.paging(n).
        # Each page is read once its "records N to M of T" line is up; with 8
        # tabs at once the pager could lag the rows, and a page read early
        # looked like a one-page result (2026-10-15)
        parsed_rows = []
        page_no, pages = 1, 1
        while True:
            _wait_for_records(page, (page_no - 1) * 10 + 1)
            soup = BeautifulSoup(page.content(), "html.parser")
            for row in soup.select("tbody tr"):
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                hdn = row.find("input", {"id": "hdnIFS"})
                internal_id = hdn["value"] if hdn else ""
                if len(cells) < 8:
                    continue
                status_raw = cells[8] if len(cells) > 8 else ""
                status = status_raw.lower() if status_raw else "unknown"
                parsed_rows.append((cells, internal_id, status))
            m = re.search(r"Page \d+ of (\d+)", soup.get_text(" ", strip=True))
            pages = min(int(m.group(1)), MAX_PAGES) if m else 1
            if page_no >= pages:
                break
            page_no += 1
            page.evaluate(f"() => $xhtml.paging({page_no})")
        if m and int(m.group(1)) > MAX_PAGES:
            logger.warning("NY search %r has %s result pages; read the first %d", search_term, m.group(1), MAX_PAGES)

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
                    "query_name": search_term,
                    "internal_id": internal_id,
                },
            ))
        time.sleep(0.5)
    except Exception as e:
        logger.error("NY search failed for %r: %s", search_term, e)
        health.failed("UCC NY searches", f"{search_term}: {e}")
    return results


def search_ny(search_term: str, is_individual: bool = False) -> list[UCCFiling]:
    """Search a single name. Opens/closes its own browser.
    NOTE: as of 2026-09, this headless path always returns 0 filings --
    the Cloudflare Turnstile challenge blocks headless Chrome entirely.
    Use search_ny_batch_cdp() (requires a real Chrome running with
    --remote-debugging-port=9222) until the block is resolved another way.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=BROWSER_ARGS)
        page = browser.new_page()
        results = _search_one(page, search_term, is_individual=is_individual)
        browser.close()
    return results


def _search_chunk_cdp(cdp_url: str, terms: list[tuple[str, bool]], out: list, tab: dict) -> None:
    """Run one worker's share of (name, is_individual) terms serially
    against a single page (tab) it opens once and reuses -- called from a
    run_tab_workers thread. Each thread gets its own
    sync_playwright()/connect_over_cdp() connection and page (Playwright's
    sync API requires objects stay within the thread that created them),
    all attached to the SAME running Chrome.
    Each worker also gets its own browser context, i.e. its own portal
    session: the portal pages results by the session's last search, so
    tabs sharing one session got each other's page 2 (2026-10-15). A fresh
    context passes Cloudflare Turnstile by itself (tested 2026-10-15).
    Results go into `out` as they come, so a stuck tab can't lose them."""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.new_context()
        page = context.new_page()
        tab["id"] = page_target_id(context, page)
        for name, is_individual in terms:
            filings = _search_one(page, name, is_individual=is_individual)
            logger.info("NY UCC %s (%s, cdp) → %d filings", name, "individual" if is_individual else "org", len(filings))
            out.extend(filings)
        tab["searched"] = time.time()
        try:
            context.close()
        except Exception as e:
            logger.warning("NY UCC tab didn't close cleanly: %s", e)


def search_ny_batch_cdp(
    org_names: list[str] = None,
    individual_names: list[str] = None,
    cdp_url: str = CDP_URL,
    max_workers: int = 8,
) -> list[UCCFiling]:
    """Search via a real, already-running Chrome reached over CDP --
    confirmed 2026-09-15 to pass the Cloudflare Turnstile challenge that
    blocks the headless path (search_ny/search_ny_batch) entirely.

    Launches its own Chrome (with a debug port) automatically if one isn't
    already running -- see _ensure_chrome_cdp() -- so this can run
    unattended from cron/run_pipeline.sh with no manual browser step.
    Same underlying pattern as pa_playwright.py/ca_playwright.py. Unlike
    PA (which hits a JSON API directly via fetch()), NY has no such API --
    this drives the real search form and reuses _search_one's existing
    HTML parsing, which already expects exactly the table shape the
    portal returns (9 columns, debtor name at index 3).

    Runs max_workers tabs in parallel within the ONE real Chrome window
    (see _search_chunk_cdp) rather than one search at a time -- NY's
    individual-name list alone can run into the thousands, and at ~10s/
    query serially that's multiple hours. 8 workers tested clean
    2026-09-15 (147 names, zero errors) and is the current default; since
    2026-10-15 each worker has its own context (see _search_chunk_cdp). Still haven't
    tested beyond 8 or verified whether NY's infra treats a burst of
    concurrent requests differently from the same volume spread serially,
    so ramp further increases incrementally rather than jumping far past
    8 untested.
    """
    _ensure_chrome_cdp(cdp_url)
    terms = [(n, False) for n in (org_names or [])] + [(n, True) for n in (individual_names or [])]
    if not terms:
        return []

    chunks = [terms[i::max_workers] for i in range(max_workers)]
    chunks = [c for c in chunks if c]
    logger.info("NY UCC: %d terms split across %d workers", len(terms), len(chunks))
    port = int(cdp_url.rsplit(":", 1)[1])
    return run_tab_workers(lambda chunk, out, tab: _search_chunk_cdp(cdp_url, chunk, out, tab), chunks, "NY UCC", port=port)


def search_ny_batch(
    org_names: list[str] = None,
    individual_names: list[str] = None,
    max_workers: int = 4,
) -> list[UCCFiling]:
    """Search organization and individual names in parallel using multiple
    browser instances. Individual names are routed to the portal's
    Individual debtor search — see _search_one."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    terms = [(n, False) for n in (org_names or [])] + [(n, True) for n in (individual_names or [])]
    all_results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(search_ny, name, is_individual): (name, is_individual) for name, is_individual in terms}
        for future in as_completed(futures):
            name, is_individual = futures[future]
            try:
                filings = future.result()
                logger.info("NY UCC %s (%s) → %d filings", name, "individual" if is_individual else "org", len(filings))
                all_results.extend(filings)
            except Exception as e:
                logger.warning("NY UCC failed for %r: %s", name, e)
                health.failed("UCC NY searches", f"{name}: {e}")
    return all_results


# save_ucc_filings and its detail_url computation live in ucc/audit_log.py
# (used across states, not NY-specific).


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_ny_batch(org_names=["BVRNC OPERATING LLC", "CORTLAND ACQUISITION LLC"])
    for r in results:
        print(r.filing_number, "|", r.debtor_name, "|", r.secured_party_name, "|", r.status)
