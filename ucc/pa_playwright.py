"""
ucc/pa_playwright.py
Pennsylvania UCC search via JSON API.
Uses Chrome CDP (connect_over_cdp) to bypass Cloudflare.
Requires Chrome running with --remote-debugging-port=9222.
Start Chrome: /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug
"""
from __future__ import annotations
import logging
import json
from datetime import datetime, date
from playwright.sync_api import sync_playwright
from ucc.base import UCCFiling

logger = logging.getLogger(__name__)
BASE_URL = "https://file.dos.pa.gov"
SEARCH_API = "/api/Records/uccsearch"
CDP_URL = "http://localhost:9222"

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

def search_pa_batch(owner_names: list[str], search_type: str = "DEBTOR") -> list[UCCFiling]:
    """
    Search PA UCC via Chrome CDP.
    Requires Chrome running: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug
    Then navigate Chrome to file.dos.pa.gov/search/ucc before calling this.
    """
    all_results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        page = context.pages[0]
        logger.info("Connected to Chrome CDP, URL: %s", page.url)
        
        for name in owner_names:
            all_results.extend(_search_one(page, name, search_type))
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
