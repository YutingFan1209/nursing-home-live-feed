"""
ucc/ca_playwright.py
California UCC search via JSON API (bizfileonline.sos.ca.gov/search/ucc).
Behind Imperva Incapsula bot detection -- plain requests and even bare
headless Playwright (chromium.launch(headless=True)) get served a JS
challenge stub instead of real data. Confirmed only a real, human-
fingerprinted Chrome instance gets through, so this uses Chrome CDP
(connect_over_cdp) exactly like ucc/pa_playwright.py.
Requires Chrome running with --remote-debugging-port=9222.
Start Chrome: /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug
Then navigate that Chrome window to bizfileonline.sos.ca.gov/search/ucc
before calling this (mirrors PA's setup) -- the page load is what lets
Incapsula issue its session cookie; hitting the bare API first fails.

Unlike NY (debtor-only) and PA (SEARCH_TYPE: DEBTOR vs SECURED_PARTY),
CA's single SEARCH_VALUE field is a unified index over both debtor and
secured-party names -- confirmed by searching a known lender name
("MIDCAP FINANCIAL") and getting 84 hits all matching on SEC_PARTY with
unrelated debtor names. So one search per term already covers both
roles; no org/individual or debtor/secured-party mode split needed.
"""
from __future__ import annotations
import logging
from datetime import datetime, date
from playwright.sync_api import sync_playwright
from ucc.base import UCCFiling

logger = logging.getLogger(__name__)
BASE_URL = "https://bizfileonline.sos.ca.gov"
SEARCH_API = "/api/Records/uccsearch"
CDP_URL = "http://localhost:9222"


def _parse_date(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date()
    except Exception:
        return None


def _split_name_city(parts) -> str:
    """TITLE/SEC_PARTY come back as single-element arrays formatted
    'NAME - CITY, ST' -- but come back as null (not even an empty list)
    for some non-financing-statement record types (tax liens, judgments)
    that don't carry a debtor-style name, so this must tolerate None.
    rsplit on ' - ' (not split) so a hyphen inside the name itself
    doesn't break the city/state off early."""
    if not parts:
        return ""
    raw = parts[0] if isinstance(parts, list) else parts
    if not raw:
        return ""
    name, _, _city_state = raw.rpartition(" - ")
    return (name or raw).strip()


def _search_one(page, search_term: str) -> list[UCCFiling]:
    results = []
    try:
        payload = {
            "SEARCH_VALUE": search_term,
            "STATUS": "ALL",
            "RECORD_TYPE_ID": "0",
            "FILING_DATE": {"start": None, "end": None},
            "LAPSE_DATE": {"start": None, "end": None},
        }
        result = page.evaluate(
            """(payload) => fetch('""" + SEARCH_API + """', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            }).then(r => r.json())""",
            payload,
        )
        page.wait_for_timeout(300)

        for record_id, row in (result.get("rows") or {}).items():
            status = (row.get("STATUS") or "unknown").lower()
            results.append(UCCFiling(
                state="CA",
                debtor_name=_split_name_city(row.get("TITLE")),
                secured_party_name=_split_name_city(row.get("SEC_PARTY")),
                filing_number=row.get("RECORD_NUM", ""),
                filing_date=_parse_date(row.get("FILING_DATE", "")),
                lapse_date=_parse_date(row.get("LAPSE_DATE", "")),
                filing_type=row.get("RECORD_TYPE", "UCC"),
                status=status,
                source_url=BASE_URL + "/search/ucc",
                raw={"query_name": search_term, "record_id": record_id},
            ))
        logger.info("CA UCC %s → %d filings", search_term, len(results))
    except Exception as e:
        logger.error("CA search failed for %r: %s", search_term, e)
    return results


def search_ca_batch(search_terms: list[str]) -> list[UCCFiling]:
    """
    Search CA UCC via Chrome CDP.
    Requires Chrome running: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug
    Then navigate Chrome to bizfileonline.sos.ca.gov/search/ucc before calling this.
    search_terms can mix CHOW facility-LLC names and CMS individual owner
    names -- no mode split needed, see module docstring.
    """
    all_results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        page = context.pages[0]
        logger.info("Connected to Chrome CDP, URL: %s", page.url)

        for term in search_terms:
            all_results.extend(_search_one(page, term))
    return all_results


def search_ca(search_term: str) -> list[UCCFiling]:
    return search_ca_batch([search_term])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_ca_batch(["COVENANT CARE CALIFORNIA"])
    for r in results:
        print(r.filing_number, "|", r.debtor_name[:40], "|", r.secured_party_name[:30], "|", r.status)
