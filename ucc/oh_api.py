from __future__ import annotations
import logging
import json
from datetime import datetime, date
from playwright.sync_api import sync_playwright
from ucc.base import UCCFiling

logger = logging.getLogger(__name__)
BASE_URL = "https://ucc.ohiosos.gov"

def _parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None

def search_oh_batch(owner_names: list[str], include_lapsed: bool = False) -> list[UCCFiling]:
    all_results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # Load dashboard to pass Cloudflare + get session
        page.goto(BASE_URL + "/dashboard")
        page.wait_for_timeout(3000)
        # Trigger auth
        page.evaluate("""
            fetch('/api/authorization/login/application', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: '{}'
            })
        """)
        page.wait_for_timeout(1000)

        for name in owner_names:
            try:
                result = page.evaluate(f"""
                    fetch('/api/ohiosearch', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            personInd: false,
                            debtorSearch: true,
                            securedPartySearch: false,
                            organizationName: {json.dumps(name)},
                            firstName: null,
                            lastName: null,
                            middleName: null,
                            city: null,
                            stateId: null,
                            activeFilingStatusPlusLapseWithinAYear: {str(include_lapsed).lower()}
                        }})
                    }}).then(r => r.json())
                """)
                page.wait_for_timeout(500)

                for item in (result.get("data") or []):
                    debtors = item.get("debtorList", [])
                    secured_parties = item.get("securePartyList", [])
                    lapse_date = _parse_date(item.get("lapseDate", ""))
                    filing_date = _parse_date(item.get("finacialStatementDate", ""))
                    status = "active" if lapse_date and lapse_date > date.today() else "lapsed"
                    all_results.append(UCCFiling(
                        state="OH",
                        debtor_name=", ".join(debtors),
                        secured_party_name=", ".join(secured_parties),
                        filing_number=item.get("finacialStatementNumber", ""),
                        filing_date=filing_date,
                        lapse_date=lapse_date,
                        filing_type=item.get("transactionCode", ""),
                        status=status,
                        source_url=f"{BASE_URL}/company-profile/search/{item.get('entityId','')}",
                        raw={"query_name": name},
                    ))
                logger.info("OH UCC %s → %d filings", name, len(result.get("data") or []))
            except Exception as e:
                logger.error("OH search failed for %r: %s", name, e)
        browser.close()
    return all_results


def search_oh(owner_name: str) -> list[UCCFiling]:
    return search_oh_batch([owner_name])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_oh_batch(["OHIO LIVING", "TRILOGY"])
    for r in results:
        print(r.filing_number, "|", r.debtor_name[:40], "|", r.secured_party_name[:30], "|", r.status)
