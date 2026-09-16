"""
ucc/pa_playwright.py
Pennsylvania UCC search via JSON API.
Uses Chrome CDP (connect_over_cdp) to bypass Incapsula.

CONFIRMED 2026-09-16: this no longer needs an actual human to manually
launch Chrome and navigate first -- the same auto-launch + self-navigate
approach that fixed NY's Cloudflare block works here too. Incapsula (like
Cloudflare) just needs a real, non-Playwright-launched Chrome to render
the page; it doesn't care whether a human or our own code drove the
navigation. search_pa_batch now calls ensure_chrome_cdp() and navigates
itself before searching, so this runs unattended like NY/KY/OH do.
"""
from __future__ import annotations
import logging
import json
from datetime import datetime, date
from playwright.sync_api import sync_playwright
from ucc.base import UCCFiling
from ucc.chrome_cdp import CDP_URL, ensure_chrome_cdp

logger = logging.getLogger(__name__)
BASE_URL = "https://file.dos.pa.gov"
SEARCH_URL = BASE_URL + "/search/ucc"
SEARCH_API = "/api/Records/uccsearch"

def _parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None

def _search_one(page, owner_name: str, search_type: str = "DEBTOR") -> list[UCCFiling]:
    results = []
    try:
        payload = {
            "SEARCH_VALUE": "",
            "SEARCH_TYPE": search_type,
            "NAME_TYPE_ID": "2",
            "ORGANIZATION_NAME": owner_name,
            "INDIVIDUAL_NAME": {"FIRST_NAME": "", "MIDDLE_NAME": "", "LAST_NAME": "", "SUFFIX": ""},
            "SEARCH_CITY": "",
            "SEARCH_STATE": "",
            "SEARCH_LAPSED": True,
            "FILING_DATE": {"start": None, "end": None},
        }
        result = page.evaluate(f"""
            fetch('{SEARCH_API}', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({json.dumps(payload)})
            }}).then(r => r.json())
        """)
        page.wait_for_timeout(300)

        today = date.today()
        for row_id, row in (result.get("rows") or {}).items():
            status_raw = row.get("STATUS", "").lower()
            status = "active" if "active" in status_raw and "inactive" not in status_raw else "inactive"
            results.append(UCCFiling(
                state="PA",
                debtor_name=row.get("DEBTOR", ""),
                secured_party_name=row.get("SEC_PARTY", ""),
                filing_number=row.get("RECORD_NUM", ""),
                filing_date=_parse_date(row.get("FILING_DATE", "")),
                lapse_date=_parse_date(row.get("LAPSE_DATE", "")),
                filing_type=row.get("RECORD_TYPE", ""),
                status=status,
                source_url=BASE_URL + "/search/ucc",
                raw={"query_name": owner_name, "record_id": row_id},
            ))
        logger.info("PA UCC %s (%s) → %d filings", owner_name, search_type, len(results))
    except Exception as e:
        logger.error("PA search failed for %r: %s", owner_name, e)
    return results

def search_pa_batch(owner_names: list[str], search_type: str = "DEBTOR", cdp_url: str = CDP_URL) -> list[UCCFiling]:
    """
    Search PA UCC via Chrome CDP. Auto-launches a real Chrome (shared with
    NY/OH's) if one isn't already running, and navigates to the search
    page itself -- no manual browser setup needed, see module docstring.
    """
    ensure_chrome_cdp(cdp_url)
    all_results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.goto(SEARCH_URL, timeout=30000)
        page.wait_for_timeout(3000)
        logger.info("PA search page loaded: %s", page.url)

        for name in owner_names:
            all_results.extend(_search_one(page, name, search_type))
        page.close()
    return all_results

def search_pa(owner_name: str) -> list[UCCFiling]:
    return search_pa_batch([owner_name])

def search_pa_by_secured_party(lender_name: str) -> list[UCCFiling]:
    return search_pa_batch([lender_name], search_type="SECURED_PARTY")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_pa_batch(["THE MEADOWS AT GETTYSBURG FOR NURSING AND REHABILITATION LLC"])
    for r in results:
        print(r.filing_number, "|", r.debtor_name[:40], "|", r.secured_party_name[:30], "|", r.status)
