"""
ucc/me.py

Maine's free "Unofficial Debtor Search" (separate from the paid official
search at apps1.web.maine.gov/cgi-bin/online/ucc/index.pl, which requires
a credit card or InforME subscription). The free unofficial flow starts
at a "begin" step:

    https://www1.maine.gov/cgi-bin/online/ucc/search/cc/search_cc_begin.pl

IMPORTANT — this site returned ROBOTS_DISALLOWED when fetched directly
during research, meaning its robots.txt blocks automated crawling. Before
scraping this in production:
  1. Check the live robots.txt yourself (maine.gov/robots.txt) and confirm
     whether the /cgi-bin/online/ucc/ path specifically is disallowed —
     a site-wide disallow doesn't necessarily mean every sub-path is
     blocked for all user-agents, but you should verify rather than
     assume.
  2. If it IS disallowed, scraping it programmatically would violate the
     state's stated access policy even though the search itself is free
     for manual/human use. Worth asking Rahul/Dr. Braun whether manual
     lookups (a research assistant doing periodic manual searches) is
     acceptable for Maine specifically, rather than an automated adapter,
     OR whether Maine should be dropped from the automated pilot and kept
     as a manual spot-check state.

Given that uncertainty, this adapter is written but should NOT be wired
into the scheduled pipeline until #1/#2 above are resolved — treat it as
ready-to-test-manually, not ready-to-automate.

CONFIRMED LIMITATION (from SOS site copy): the free unofficial search
only covers active filings + filings lapsed within the past 1 year, and
is described as a name-variation finder rather than a full-detail
report. It may not surface secured-party name (the field your RE/PE
classifier depends on) without the paid pull — verify this once you can
run one search by hand. If secured-party data isn't in the free tier,
Maine becomes a "confirms a filing exists, doesn't tell you who the
lender is" source, which is still useful as a secondary signal but can't
feed lender_classifier.py directly.

This is a CGI/Perl wizard, not ASP.NET — uses ucc/cgi_form.py, not
aspnet_form.py. No viewstate, but likely session-cookie continuity
across steps (begin -> enter name -> results).

FIELD_MAP values are PLACEHOLDERS — run
`scripts/discover_form_fields.py https://www1.maine.gov/cgi-bin/online/ucc/search/cc/search_cc_begin.pl`
locally (after confirming robots.txt allows it) to get real field names.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .base import UCCFiling, UCCSource, IngestMethod
from . import cgi_form

BEGIN_URL = "https://www1.maine.gov/cgi-bin/online/ucc/search/cc/search_cc_begin.pl"

# PLACEHOLDER — replace after running discover_form_fields.py
FIELD_MAP = {
    "debtor_name": "org_name",      # guess — CGI forms tend to use plainer names than ASP.NET
    "search_type": "search_type",   # guess — likely "organization" vs "individual"
    "submit_button": "submit",      # guess
}


class MaineUCCSource(UCCSource):
    state = "ME"
    ingest_method = IngestMethod.SCRAPE
    is_free = True  # free tier only — confirm secured-party data is included (see module docstring)

    def search(self, debtor_name: str) -> list[UCCFiling]:
        """Two-step wizard: GET the begin page for the form + session
        cookie, then POST the debtor name to whatever action URL that
        page's form points to. NOT yet verified live — see module
        docstring re: robots.txt and field names before automating this."""
        session = requests.Session()
        fields, action_url = cgi_form.get_form_fields(session, BEGIN_URL)

        fields[FIELD_MAP["debtor_name"]] = debtor_name
        fields[FIELD_MAP["search_type"]] = "organization"

        response = cgi_form.submit_form(session, action_url, fields, method="POST")
        return self._parse_search_results(response.text)

    def _parse_search_results(self, html: str) -> list[UCCFiling]:
        """Placeholder parser. Maine's unofficial search results are
        likely a simple table or definition-list of name-variation
        matches, NOT necessarily including secured-party — confirm shape
        once a real result page is available. Structured to degrade
        gracefully: if secured_party_name isn't found in a row, it's left
        empty rather than failing, since that's an expected possibility
        for this particular source per the docstring above."""
        soup = BeautifulSoup(html, "html.parser")
        filings = []

        results_table = soup.find("table", id="results")  # guess
        if not results_table:
            return filings

        for row in results_table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 2:
                continue
            # Defensive unpacking — column count/order unconfirmed
            filing_number = cells[0] if len(cells) > 0 else ""
            debtor = cells[1] if len(cells) > 1 else ""
            filing_date_str = cells[2] if len(cells) > 2 else ""
            secured_party = cells[3] if len(cells) > 3 else ""  # may not exist in free tier

            filings.append(
                UCCFiling(
                    state=self.state,
                    debtor_name=debtor,
                    secured_party_name=secured_party,
                    filing_number=filing_number,
                    filing_date=_parse_date(filing_date_str),
                    source_url=BEGIN_URL,
                    raw={"cells": cells, "secured_party_confirmed": bool(secured_party)},
                )
            )
        return filings


def _parse_date(value: str) -> Optional[object]:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None
