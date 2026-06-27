"""
ucc/pa_playwright.py
Pennsylvania UCC search via JSON API (Cloudflare bypass with Playwright).
POST https://file.dos.pa.gov/api/Records/uccsearch
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
BROWSER_ARGS = ["--disable-blink-features=AutomationControlled", "--window-position=-10000,-10000"]
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"

def _parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None

def search_pa_batch(owner_names: list[str], include_lapsed: bool = True) -> list[UCCFiling]:
    all_results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=BROWSER_ARGS)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        # Load page to get Cloudflare cookies
        page.goto(BASE_URL + "/search/ucc")
        page.wait_for_timeout(3000)

        for name in owner_names:
            try:
                result = page.evaluate(f"""
                    fetch('{SEARCH_API}', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            SEARCH_VALUE: "",
                            SEARCH_TYPE: "DEBTOR",
                            NAME_TYPE_ID: "2",
                            ORGANIZATION_NAME: {json.dumps(name)},
                            INDIVIDUAL_NAME: {{FIRST_NAME: "", MIDDLE_NAME: "", LAST_NAME: "", SUFFIX: ""}},
                            SEARCH_CITY: "",
                            SEARCH_STATE: "",
                            SEARCH_LAPSED: {str(include_lapsed).lower()},
                            FILING_DATE: {{start: null, end: null}}
                        }})
                    }}).then(r => r.json())
                """)
                page.wait_for_timeout(500)

                today = date.today()
                for row_id, row in (result.get("rows") or {}).items():
                    sec_party = row.get("SEC_PARTY", "")
                    status_raw = row.get("STATUS", "").lower()
                    status = "active" if "active" in status_raw and "inactive" not in status_raw else "inactive"
                    lapse_date = _parse_date(row.get("LAPSE_DATE", ""))
                    filing_date = _parse_date(row.get("FILING_DATE", ""))

                    all_results.append(UCCFiling(
                        state="PA",
                        debtor_name=row.get("DEBTOR", ""),
                        secured_party_name=sec_party,
                        filing_number=row.get("RECORD_NUM", ""),
                        filing_date=filing_date,
                        lapse_date=lapse_date,
                        filing_type=row.get("RECORD_TYPE", ""),
                        status=status,
                        source_url=BASE_URL + "/search/ucc",
                        raw={"query_name": name, "record_id": row_id},
                    ))
                logger.info("PA UCC %s → %d filings", name, len(result.get("rows") or {}))
            except Exception as e:
                logger.error("PA search failed for %r: %s", name, e)
            page.wait_for_timeout(500)

        browser.close()
    return all_results


def search_pa(owner_name: str) -> list[UCCFiling]:
    return search_pa_batch([owner_name])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_pa_batch(["THE MEADOWS AT GETTYSBURG FOR NURSING AND REHABILITATION LLC"])
    for r in results:
        print(r.filing_number, "|", r.debtor_name[:40], "|", r.secured_party_name[:30], "|", r.status)
