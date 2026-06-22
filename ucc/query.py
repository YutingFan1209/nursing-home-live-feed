"""
ucc/query.py
------------
CMS owner name → UCC portal query, by state.

Pilot states: NY, NJ, KY, ME, PA, DE
Usage:
    from ucc.query import run_ucc_query
    results = run_ucc_query(owner_names=["John Smith", "ABC Care LLC"], states=["NY", "NJ", "KY"])

Each result is a UCCFiling dataclass with normalized fields.
Lender classification (HIGH/MEDIUM/LOW) is done downstream in ucc/classify.py.

State coverage:
    KY  — bulk flat-file download (authenticated, free for research)
    NY  — form-POST scraper (legacy Oracle app, debtor name search)
    NJ  — form-POST scraper (njportal.com, wildcard-capable)
    ME  — CGI form scraper (free unofficial search, robots.txt verified before use)
    PA  — form-POST scraper ($12/search certified; unofficial free tier)
    DE  — NOT automatable; vendor-mediated only. Raises NotImplementedError.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class UCCFiling:
    state: str
    debtor_name: str                    # as returned by portal
    secured_party: str                  # lender / secured party name
    filing_number: str
    filing_date: str                    # ISO string or raw portal string
    filing_type: str                    # INITIAL / AMENDMENT / CONTINUATION / TERMINATION
    collateral_description: str
    status: str                         # Active / Lapsed / Unknown
    query_name: str                     # the CMS owner name used to find this
    source_url: str = ""
    raw: dict = field(default_factory=dict)   # preserve raw portal response


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------

class UCCStateAdapter:
    """Override query() in each state subclass."""
    state: str = ""

    def query(self, owner_name: str) -> list[UCCFiling]:
        raise NotImplementedError(f"{self.state} adapter not implemented")

    def _get(self, url: str, **kwargs) -> requests.Response:
        """Shared GET with retry."""
        return _http_get(url, **kwargs)

    def _post(self, url: str, data: dict, **kwargs) -> requests.Response:
        """Shared POST with retry."""
        return _http_post(url, data=data, **kwargs)


# ---------------------------------------------------------------------------
# HTTP helpers (shared, with retry)
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; HEFTI-UCC-Research/1.0; "
        "+https://hefti.weill.cornell.edu)"
    )
}

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _http_get(url: str, **kwargs) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=15, **kwargs)
    resp.raise_for_status()
    return resp

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _http_post(url: str, data: dict, **kwargs) -> requests.Response:
    resp = requests.post(url, data=data, headers=HEADERS, timeout=15, **kwargs)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# State adapters
# ---------------------------------------------------------------------------

class NYAdapter(UCCStateAdapter):
    """
    New York — NY Dept. of State UCC Public Inquiry System.
    Legacy Oracle PL/SQL form-POST app.
    URL: https://appext20.dos.ny.gov/pls/ucc_public/web_search.main_frame
    Free, no login. Debtor-name search only (no date-range browse).
    Returns: debtor name, secured party, file number, file date, status.
    NOTE: collateral description requires a separate per-filing fetch ($5 copy
    request) — not included here; set to empty string.
    """
    state = "NY"
    SEARCH_URL = "https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineUCCSearch"

def query(self, owner_name: str) -> list[UCCFiling]:
    results = []
    try:
        # First GET to grab VIEWSTATE
        landing = self._get(self.SEARCH_URL)
        soup = BeautifulSoup(landing.text, "html.parser")
        vs = soup.find("input", {"id": "__VIEWSTATE"})
        ev = soup.find("input", {"id": "__EVENTVALIDATION"})

        data = {
            "__VIEWSTATE": vs["value"] if vs else "",
            "__EVENTVALIDATION": ev["value"] if ev else "",
            "rdbType": "DebtorName",
            "ddlSearchLogic": "SW",
            "rdbDebtorType": "Organization",
            "UCCSearch_UCCSearach_txtOrgName": owner_name,
            "ddlLienStatus": "0",
            "UCCSearch_UCCSearach_txtOrgID": "",
            "UCCSearch_UCCSearach_selectOrgType": "",
            "UCCSearch_UCCSearach_txtOrgJur": "",
            "UCCSearch_UCCSearach_txtLastName": "",
            "UCCSearch_UCCSearach_txtFirstName": "",
            "UCCSearch_UCCSearach_txtMiddleName": "",
            "ddlSuffix": "",
            "ddlLienType": "",
            "ddlFilingType": "",
            "ddlLienActionType": "",
            "UCCSearch_UCCSearach_txtFilingDateFrom": "",
            "UCCSearch_UCCSearach_txtFilingDateTo": "",
            "UCCSearch_UCCSearach_txtLapseDateFrom": "",
            "UCCSearch_UCCSearach_txtLapseDateTo": "",
            "searchType": "",
            "hdnSuffixDesc": "Select",
        }
        resp = self._post(self.SEARCH_URL, data=data)
        soup2 = BeautifulSoup(resp.text, "html.parser")
        results = self._parse(soup2, owner_name)
    except Exception as exc:
        logger.warning("NY query failed for %r: %s", owner_name, exc)
    return results


class NJAdapter(UCCStateAdapter):
    """
    New Jersey — NJ Division of Revenue UCC portal (njportal.com/ucc).
    Wildcard / partial-name search supported.
    Per-record fee: ~$0.0235 (law $0.0185 + portal $0.005).
    Non-certified search is free for preview; bulk billed at query time.
    TODO: Confirm whether session/token is required before POSTing.
    """
    state = "NJ"
    SEARCH_URL = "https://www.njportal.com/ucc/SearchUCC/Search.aspx"

    def query(self, owner_name: str) -> list[UCCFiling]:
        results = []
        try:
            # NJ is an ASP.NET WebForms app — needs __VIEWSTATE extracted first.
            landing = self._get(self.SEARCH_URL)
            soup = BeautifulSoup(landing.text, "html.parser")
            viewstate = soup.find("input", {"id": "__VIEWSTATE"})
            vs_val = viewstate["value"] if viewstate else ""
            eventval = soup.find("input", {"id": "__EVENTVALIDATION"})
            ev_val = eventval["value"] if eventval else ""

            data = {
                "__VIEWSTATE": vs_val,
                "__EVENTVALIDATION": ev_val,
                "ctl00$MainContent$txtDebtorName": owner_name,
                "ctl00$MainContent$btnSearch": "Search",
                "ctl00$MainContent$ddlSearchType": "DEBTOR",
                "ctl00$MainContent$chkIncludeLapsed": "on",
            }
            resp = self._post(self.SEARCH_URL, data=data)
            soup2 = BeautifulSoup(resp.text, "html.parser")
            results = self._parse(soup2, owner_name)
        except Exception as exc:
            logger.warning("NJ query failed for %r: %s", owner_name, exc)
        return results

    def _parse(self, soup: BeautifulSoup, owner_name: str) -> list[UCCFiling]:
        filings = []
        # TODO: fill in actual NJ results table selectors after manual inspection.
        rows = soup.select("table#SearchResults tr")[1:]
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 4:
                continue
            filings.append(UCCFiling(
                state="NJ",
                debtor_name=cells[0],
                secured_party=cells[1],
                filing_number=cells[2],
                filing_date=cells[3],
                filing_type=cells[4] if len(cells) > 4 else "",
                collateral_description=cells[5] if len(cells) > 5 else "",
                status=cells[6] if len(cells) > 6 else "Unknown",
                query_name=owner_name,
                source_url=self.SEARCH_URL,
            ))
        return filings


class KYAdapter(UCCStateAdapter):
    """
    Kentucky — two modes:
    (A) BULK (preferred): authenticated flat-file download from Kentucky.gov
        bulk data service. Requires Subscriber Agreement + approval.
        Call KYBulkLoader (separate module) instead of this adapter.
    (B) LIVE SEARCH (fallback): sos.ky.gov ASP.NET WebForms portal.
        Use when bulk credentials are not yet set up.
    This adapter implements mode (B) as fallback.
    """
    state = "KY"
    SEARCH_URL = "https://web.sos.ky.gov/ftucc/search.aspx"

    def query(self, owner_name: str) -> list[UCCFiling]:
        results = []
        try:
            landing = self._get(self.SEARCH_URL)
            soup = BeautifulSoup(landing.text, "html.parser")
            vs = soup.find("input", {"id": "__VIEWSTATE"})
            ev = soup.find("input", {"id": "__EVENTVALIDATION"})

            data = {
                "__VIEWSTATE": vs["value"] if vs else "",
                "__EVENTVALIDATION": ev["value"] if ev else "",
                "ctl00$ContentPlaceHolder1$txtDebtorName": owner_name,
                "ctl00$ContentPlaceHolder1$btnSearch": "Search",
            }
            resp = self._post(self.SEARCH_URL, data=data)
            soup2 = BeautifulSoup(resp.text, "html.parser")
            results = self._parse(soup2, owner_name)
        except Exception as exc:
            logger.warning("KY query failed for %r: %s", owner_name, exc)
        return results

    def _parse(self, soup: BeautifulSoup, owner_name: str) -> list[UCCFiling]:
        filings = []
        # TODO: fill in actual KY results table selectors.
        rows = soup.select("table#GridView1 tr")[1:]
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 4:
                continue
            filings.append(UCCFiling(
                state="KY",
                debtor_name=cells[0],
                secured_party=cells[1],
                filing_number=cells[2],
                filing_date=cells[3],
                filing_type=cells[4] if len(cells) > 4 else "",
                collateral_description=cells[5] if len(cells) > 5 else "",
                status=cells[6] if len(cells) > 6 else "Unknown",
                query_name=owner_name,
                source_url=self.SEARCH_URL,
            ))
        return filings


class MEAdapter(UCCStateAdapter):
    """
    Maine — free unofficial debtor search (CGI Perl script).
    URL: https://www.maine.gov/cgi-bin/online/ucc/index.pl
    ⚠️  robots.txt compliance: VERIFY before running automated queries.
    Simple HTML form, no JS, no VIEWSTATE — easiest to scrape of all six states.
    Covers active filings + lapsed within 1 year of lapse only.
    """
    state = "ME"
    SEARCH_URL = "https://www.maine.gov/cgi-bin/online/ucc/index.pl"

    def query(self, owner_name: str) -> list[UCCFiling]:
        results = []
        try:
            data = {
                "action": "search",
                "debtor_name": owner_name,
                "search_type": "debtor",
            }
            resp = self._post(self.SEARCH_URL, data=data)
            soup = BeautifulSoup(resp.text, "html.parser")
            results = self._parse(soup, owner_name)
        except Exception as exc:
            logger.warning("ME query failed for %r: %s", owner_name, exc)
        return results

    def _parse(self, soup: BeautifulSoup, owner_name: str) -> list[UCCFiling]:
        filings = []
        # TODO: fill in actual ME results table selectors after manual check.
        rows = soup.select("table tr")[1:]
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 3:
                continue
            filings.append(UCCFiling(
                state="ME",
                debtor_name=cells[0],
                secured_party=cells[1],
                filing_number=cells[2],
                filing_date=cells[3] if len(cells) > 3 else "",
                filing_type=cells[4] if len(cells) > 4 else "",
                collateral_description="",
                status="Active",         # ME unofficial search = active only
                query_name=owner_name,
                source_url=self.SEARCH_URL,
            ))
        return filings


class PAAdapter(UCCStateAdapter):
    """
    Pennsylvania — PennFile (file.dos.pa.gov/search/ucc).
    ⚠️  JavaScript-heavy SPA — requires Playwright/Selenium, not plain requests.
    This adapter raises NotImplementedError until a headless-browser approach
    is confirmed feasible in the project environment.
    Alternative: contact RA-STUCC_CERTS@pa.gov for bulk subscription.
    """
    state = "PA"

    def query(self, owner_name: str) -> list[UCCFiling]:
        raise NotImplementedError(
            "PA UCC portal is a JS-heavy SPA (file.dos.pa.gov/search/ucc). "
            "Plain HTTP scraping won't work. Options:\n"
            "  (A) Use Playwright/Selenium headless browser\n"
            "  (B) Contact RA-STUCC_CERTS@pa.gov for bulk data subscription\n"
            "  (C) Per-search: $12/name via certified search request"
        )


class DEAdapter(UCCStateAdapter):
    """
    Delaware — vendor-mediated only. No public portal.
    All searches must go through a state-authorized searcher
    (CSC, Cogency Global, First Corporate Solutions, etc.).
    Exact-name-match only; no date-range browse or discovery mode.
    Use this as a verification step ONLY after identifying entity names
    from other sources (KY/NJ bulk, CMS, EDGAR, news).
    """
    state = "DE"

    def query(self, owner_name: str) -> list[UCCFiling]:
        raise NotImplementedError(
            "Delaware UCC searches cannot be automated. "
            "All searches are vendor-mediated (authorized searcher required). "
            "Submit manual search requests to First Corporate Solutions "
            "(orders@ficoso.com) or Delaware Business Incorporators "
            "(support@dbiglobal.com). Fee: $25 state + $25 expedited + vendor markup."
        )


# ---------------------------------------------------------------------------
# Registry + main entry point
# ---------------------------------------------------------------------------

ADAPTERS: dict[str, UCCStateAdapter] = {
    "NY": NYAdapter(),
    "NJ": NJAdapter(),
    "KY": KYAdapter(),
    "ME": MEAdapter(),
    "PA": PAAdapter(),
    "DE": DEAdapter(),
}


def run_ucc_query(
    owner_names: list[str],
    states: list[str] | None = None,
    delay: float = 1.5,
) -> list[UCCFiling]:
    """
    Query UCC portals for a list of CMS owner names.

    Args:
        owner_names: List of owner/operator names from CMS datasets.
        states: Which states to query. Defaults to all implemented states
                (excludes PA and DE which raise NotImplementedError).
        delay: Seconds to sleep between requests (be polite to state portals).

    Returns:
        Flat list of UCCFiling objects across all states and names.
        Pass to ucc/classify.py for HIGH/MEDIUM/LOW lender classification.
    """
    if states is None:
        states = ["NY", "NJ", "KY", "ME"]   # PA/DE excluded until resolved

    all_results: list[UCCFiling] = []

    for state in states:
        adapter = ADAPTERS.get(state.upper())
        if adapter is None:
            logger.warning("No adapter for state: %s", state)
            continue

        logger.info("Querying %s UCC portal for %d names...", state, len(owner_names))
        for name in owner_names:
            try:
                filings = adapter.query(name)
                logger.info("  %s | %r → %d results", state, name, len(filings))
                all_results.extend(filings)
            except NotImplementedError as e:
                logger.warning("  %s | skipped: %s", state, str(e).splitlines()[0])
            except Exception as e:
                logger.error("  %s | %r → error: %s", state, name, e)
            time.sleep(delay)

    logger.info("Total UCC filings retrieved: %d", len(all_results))
    return all_results


# ---------------------------------------------------------------------------
# CMS owner name loader (pulls from existing cms/fetch_cms.py output)
# ---------------------------------------------------------------------------

def load_cms_owner_names(cms_data: list[dict]) -> list[str]:
    """
    Extract unique owner/operator names from CMS dataset rows.
    Input: rows from CMS datasets qhpq-qrm6 or 4pq5-n9py.
    Returns: deduplicated list of name strings, stripped and title-cased.
    """
    names = set()
    for row in cms_data:
        for field in ("owner_name", "operator_name", "provider_name", "buyer", "seller"):
            val = row.get(field, "")
            if val and isinstance(val, str):
                names.add(val.strip())
    return sorted(names)


# ---------------------------------------------------------------------------
# CLI for quick testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Example: test with a few known names
    test_names = ["ABC Healthcare LLC", "Smith Nursing Home"]
    test_states = ["NY", "NJ", "KY", "ME"]

    results = run_ucc_query(owner_names=test_names, states=test_states)
    print(json.dumps(
        [vars(r) for r in results],
        indent=2, default=str
    ))
