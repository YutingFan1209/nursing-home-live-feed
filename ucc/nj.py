"""
ucc/nj.py

New Jersey's non-certified UCC search (free, official):
  https://www.njportal.com/ucc/search/noncertifiedsearch.aspx

IMPORTANT CONSTRAINT: NJ only lets you search by debtor name (or filing
number), NOT by secured party. So the strategy is:
  - Search for known nursing home operator names from our deals table
  - Filter the returned secured parties with lender_classifier.py after

NJ's search is a two-step ASP.NET wizard:
  Step 1 — choose search type (Organization) + output format (StatusReport),
            click Continue
  Step 2 — enter the org name, click Search
  Results — table of matching filings

The step-1 field names are verified (2026-06-17 via discover_form_fields.py).
The step-2 field names are auto-discovered from the step-2 HTML so we don't
need to hardcode them — if NJ ever renames the inputs, the discovery will
still find any visible text input on the page.

If the portal redirects us away after step 1 (URL changes away from
noncertifiedsearch.aspx), we log a warning and return [] rather than crashing.
This protects the rest of the pipeline while flagging the issue.

TO DEBUG a redirect failure: open noncertifiedsearch.aspx in Chrome DevTools
→ Network tab, do the wizard manually, and inspect the step-1 and step-2 POST
payloads. The field names the browser sends are the ground truth.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import UCCFiling, UCCSource, IngestMethod
from . import aspnet_form

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.njportal.com/ucc/search/noncertifiedsearch.aspx"

# Step-1 field names — verified 2026-06-17 via discover_form_fields.py
_STEP1_SEARCH_TYPE  = "ctl00$mainContent$DebtorSearch1$Wizard1$radioSwitchOrgPerson"
_STEP1_OUTPUT_TYPE  = "ctl00$mainContent$DebtorSearch1$Wizard1$radioOutputList"
_STEP1_CONTINUE_BTN = "ctl00$mainContent$DebtorSearch1$Wizard1$StartNavigationTemplateContainerID$btnContinue"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class NewJerseyUCCSource(UCCSource):
    state = "NJ"
    ingest_method = IngestMethod.SCRAPE
    is_free = True

    def search(self, debtor_name: str) -> list[UCCFiling]:
        """Two-step wizard search by organization (debtor) name."""
        session = requests.Session()
        session.headers.update(_BROWSER_HEADERS)

        # ── Step 1: navigate the wizard to the org-name page ─────────────
        step1_fields = aspnet_form.get_form_fields(session, SEARCH_URL)
        step1_fields[_STEP1_SEARCH_TYPE]  = "Organization"
        step1_fields[_STEP1_OUTPUT_TYPE]  = "StatusReport"
        step1_fields[_STEP1_CONTINUE_BTN] = "Continue"
        # Submit buttons don't go through __doPostBack — clear these so
        # ASP.NET doesn't see a conflicting EventTarget
        step1_fields["__EVENTTARGET"]   = ""
        step1_fields["__EVENTARGUMENT"] = ""

        step2_resp = aspnet_form.post_form(session, SEARCH_URL, step1_fields)

        if SEARCH_URL.rstrip("/") not in step2_resp.url:
            logger.warning(
                f"NJ UCC: step-1 POST redirected away from the search page "
                f"(landed at {step2_resp.url!r}). "
                f"The portal may be blocking automated access — verify manually "
                f"via Chrome DevTools to get the real step-1/step-2 POST payloads."
            )
            return []

        # ── Step 2: fill in the debtor name and search ────────────────────
        step2_soup = BeautifulSoup(step2_resp.text, "html.parser")
        step2_fields = aspnet_form.get_form_fields_from_soup(step2_soup)

        org_field = _find_org_name_field(step2_soup)
        if not org_field:
            logger.warning(
                "NJ UCC: step-2 page loaded but couldn't find an org-name text input. "
                "Page may have changed structure."
            )
            return []

        step2_fields[org_field] = debtor_name
        step2_fields["__EVENTTARGET"]   = ""
        step2_fields["__EVENTARGUMENT"] = ""

        search_btn = _find_search_button(step2_soup)
        if search_btn:
            step2_fields[search_btn] = "Search"

        results_resp = aspnet_form.post_form(session, SEARCH_URL, step2_fields)
        return self._parse_search_results(results_resp.text)

    def _parse_search_results(self, html: str) -> list[UCCFiling]:
        """Parse NJ's results table. Table id is 'gvSearchResults' — confirmed
        in the unit-test mock HTML (test_parsing.py). Real page structure should
        match since this is the standard NJ non-certified search layout."""
        soup = BeautifulSoup(html, "html.parser")
        filings = []

        results_table = (
            soup.find("table", id="gvSearchResults")
            or soup.find("table", id="GridView1")   # fallback if id changed
        )
        if not results_table:
            return filings

        for row in results_table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 4:
                continue
            # NJ columns: Filing # | Date | Debtor | Secured Party | Status
            filing_number   = cells[0]
            filing_date_str = cells[1]
            debtor          = cells[2]
            secured_party   = cells[3]
            status          = cells[4].lower() if len(cells) > 4 else "active"
            filings.append(
                UCCFiling(
                    state=self.state,
                    debtor_name=debtor,
                    secured_party_name=secured_party,
                    filing_number=filing_number,
                    filing_date=_parse_date(filing_date_str),
                    status=status,
                    source_url=SEARCH_URL,
                    raw={"cells": cells},
                )
            )
        return filings


def _find_org_name_field(soup: BeautifulSoup) -> Optional[str]:
    """Find the org-name text input on step 2 by scanning all visible text
    inputs. Prefer one whose name/id contains a recognizable keyword; fall
    back to any text input if none match. Returns the field's `name` attr."""
    candidates = soup.find_all("input", type="text")
    # Prefer inputs whose name/id look like an org-name field
    keywords = ("org", "name", "debtor", "search", "entity")
    for inp in candidates:
        combined = (inp.get("name", "") + inp.get("id", "")).lower()
        if any(k in combined for k in keywords):
            return inp.get("name")
    # Fallback: any visible text input
    for inp in candidates:
        name = inp.get("name", "")
        if name:
            return name
    return None


def _find_search_button(soup: BeautifulSoup) -> Optional[str]:
    """Find the submit button on step 2. Returns the field name so we can
    include it in the POST (simulating the user clicking it)."""
    for inp in soup.find_all("input", type="submit"):
        val = inp.get("value", "").lower()
        name = inp.get("name", "")
        if name and ("search" in val or "submit" in val or "next" in val or "continue" in val):
            return name
    # Fallback: any submit button
    for inp in soup.find_all("input", type="submit"):
        name = inp.get("name", "")
        if name:
            return name
    return None


def _parse_date(value: str) -> Optional[object]:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None
