"""
ucc/ca_playwright.py
California UCC search via JSON API (bizfileonline.sos.ca.gov/search/ucc).
Behind Imperva Incapsula bot detection -- plain requests and even bare
headless Playwright (chromium.launch(headless=True)) get served a JS
challenge stub instead of real data. Confirmed only a real, human-
fingerprinted Chrome instance gets through, so this uses Chrome CDP
(connect_over_cdp) exactly like ucc/pa_playwright.py.
search_ca_batch auto-launches that real Chrome (ucc/chrome_cdp.py, shared
with NY/PA) and loads the search page itself before calling the API, the
same fix that took PA off manual-only on 2026-09-16 -- the page load is
what lets Incapsula issue its session cookie; hitting the bare API first
fails. Expect PA's fragility too: Incapsula challenged a full PA batch
partway through, so probe with one name before trusting a big batch.

Unlike NY (debtor-only) and PA (SEARCH_TYPE: DEBTOR vs SECURED_PARTY),
CA's single SEARCH_VALUE field is a unified index over both debtor and
secured-party names -- confirmed by searching a known lender name
("MIDCAP FINANCIAL") and getting 84 hits all matching on SEC_PARTY with
unrelated debtor names. So one search per term already covers both
roles; no org/individual or debtor/secured-party mode split needed.
"""
from __future__ import annotations
import json
import logging
import re
import threading
from datetime import datetime, date
from playwright.sync_api import sync_playwright
from ucc.base import UCCFiling
from ucc.chrome_cdp import CDP_URL, ensure_chrome_cdp
from pipeline.run_health import health

logger = logging.getLogger(__name__)
BASE_URL = "https://bizfileonline.sos.ca.gov"
SEARCH_URL = BASE_URL + "/search/ucc"
SEARCH_API = "/api/Records/uccsearch"


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


# Incapsula answers sustained volume with HTTP 429 and a JSON "blocked by
# our security service" body (confirmed 2026-09-30: a 30-name, 2-tab batch
# plus a few follow-ups -- about 35 searches in two minutes -- got blocked).
# That body has no "rows", which the first version read as "0 filings", so a
# block looked like a clean run. Any response without rows now counts as a
# block and stops the whole batch.
SEARCH_DELAY_MS = 2500

# Terms actually searched by the last search_ca_batch call (a block stops
# the batch early). main.py uses it to resume the next run where this one
# stopped.
LAST_SEARCHED = 0

_SUFFIXES = re.compile(r"\b(LLC|L L C|INC|INCORPORATED|LP|L P|LLP|CORP|CORPORATION|CO|COMPANY|LTD|THE)\b")


class CABlocked(RuntimeError):
    pass


def _norm(name: str) -> str:
    name = re.sub(r"[^A-Z0-9 ]+", " ", (name or "").upper())
    return " ".join(_SUFFIXES.sub(" ", name).split())


def _is_match(term: str, debtor: str) -> bool:
    """CA's search matches loosely ("DEL ORO LLC" returned 96 filings) and
    over secured parties too, so keep only filings whose debtor is the
    searched name, allowing trailing words ("DEL ORO CARE CENTER" for
    "DEL ORO LLC"). Search terms are buyers and operators, never lenders:
    matching the secured party turned "DIGNITY COMMUNITY CARE" into 38
    filings where Dignity was the creditor (2026-09-30)."""
    q = _norm(term)
    d = _norm(debtor)
    return bool(q) and (d == q or d.startswith(q + " "))


def _search_one(page, search_term: str) -> list[UCCFiling]:
    payload = {
        "SEARCH_VALUE": search_term,
        "STATUS": "ALL",
        "RECORD_TYPE_ID": "0",
        "FILING_DATE": {"start": None, "end": None},
        "LAPSE_DATE": {"start": None, "end": None},
    }
    response = page.evaluate(
        """(payload) => fetch('""" + SEARCH_API + """', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        }).then(async r => ({status: r.status, text: await r.text()}))""",
        payload,
    )
    try:
        result = json.loads(response["text"])
    except ValueError:
        result = None
    if response["status"] != 200 or not isinstance(result, dict) or "rows" not in result:
        detail = (result or {}).get("description") if isinstance(result, dict) else response["text"][:120]
        raise CABlocked(f"HTTP {response['status']}: {detail}")

    results = []
    for record_id, row in (result.get("rows") or {}).items():
        # RECORD_TYPE_ID "0" returns every record type; only financing
        # statements ("UCC") are loans. State tax liens (e.g. Employment
        # Development Department) and judgment liens were 78 of 639 filings
        # on the first run.
        if row.get("RECORD_TYPE") != "UCC":
            continue
        debtor = _split_name_city(row.get("TITLE"))
        secured_party = _split_name_city(row.get("SEC_PARTY"))
        if not _is_match(search_term, debtor):
            continue
        results.append(UCCFiling(
            state="CA",
            debtor_name=debtor,
            secured_party_name=secured_party,
            filing_number=row.get("RECORD_NUM", ""),
            filing_date=_parse_date(row.get("FILING_DATE", "")),
            lapse_date=_parse_date(row.get("LAPSE_DATE", "")),
            filing_type=row.get("RECORD_TYPE", "UCC"),
            status=(row.get("STATUS") or "unknown").lower(),
            source_url=SEARCH_URL,
            raw={"query_name": search_term, "record_id": record_id},
        ))
    logger.info("CA UCC %s → %d filings (%d returned)", search_term, len(results), len(result.get("rows") or {}))
    return results


def _search_chunk(cdp_url: str, terms: list[str], blocked: threading.Event) -> list[UCCFiling]:
    """One worker's share of terms, run serially in its own tab after one
    real page load (for Incapsula's session cookie). Stops as soon as any
    worker has been blocked."""
    global LAST_SEARCHED
    results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.goto(SEARCH_URL, timeout=30000)
        page.wait_for_timeout(3000)
        for term in terms:
            if blocked.is_set():
                break
            try:
                results.extend(_search_one(page, term))
                LAST_SEARCHED += 1
            except CABlocked as e:
                blocked.set()
                logger.error("CA UCC blocked at %r: %s -- stopping the batch", term, e)
                health.source_failed("UCC CA", f"blocked by Incapsula at {term!r}: {e}")
                break
            except Exception as e:
                logger.error("CA search failed for %r: %s", term, e)
                health.failed("UCC CA searches", f"{term}: {e}")
            page.wait_for_timeout(SEARCH_DELAY_MS)
        page.close()
    return results


def search_ca_batch(search_terms: list[str], cdp_url: str = CDP_URL, max_workers: int = 1) -> list[UCCFiling]:
    """
    Search CA UCC via a real Chrome over CDP, auto-launched if needed (see
    module docstring). One tab with a SEARCH_DELAY_MS pause between
    searches by default: Incapsula blocked two tabs at full speed within
    about 35 searches. The batch stops at the first block, and the terms
    after it aren't searched (see health report).
    """
    global LAST_SEARCHED
    LAST_SEARCHED = 0
    if not search_terms:
        return []
    ensure_chrome_cdp(cdp_url)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    blocked = threading.Event()
    chunks = [c for c in (search_terms[i::max_workers] for i in range(max_workers)) if c]
    all_results = []
    with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
        futures = [executor.submit(_search_chunk, cdp_url, chunk, blocked) for chunk in chunks]
        for future in as_completed(futures):
            all_results.extend(future.result())
    return all_results


def search_ca(search_term: str) -> list[UCCFiling]:
    return search_ca_batch([search_term])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = search_ca_batch(["COVENANT CARE CALIFORNIA"])
    for r in results:
        print(r.filing_number, "|", r.debtor_name[:40], "|", r.secured_party_name[:30], "|", r.status)
