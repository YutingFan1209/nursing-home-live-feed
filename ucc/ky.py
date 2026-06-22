"""
ucc/ky.py

Kentucky is the priority state: it has a free Bulk Data Service for
non-commercial/research use that already includes full UCC filings,
collateral, and secured-party/debtor records (monthly refresh + daily/
weekly new-filing feeds) — see
https://www.sos.ky.gov/bus/Pages/Bulk-Data-Service.aspx

ACTION NEEDED FROM YUTING (not something I can do from here):
  1. Register a Kentucky.gov account
  2. Complete the Bulk Data Service Subscriber Agreement, specifying
     "non-commercial / research" use (HEFTI qualifies — no monthly fee)
  3. Confirm the actual file format/delivery method (FTP? HTTPS download?
     CSV/fixed-width/XML?) — the agreement page didn't specify this
     publicly, so `bulk_ingest()` below has a best-guess CSV parser that
     WILL need adjusting once you see a real file.

The scrape-based fallback (KentuckyUCCSource.search) hits the free
public index search (web.sos.ky.gov/ftucc/search.aspx) directly — useful
if bulk access takes a while to set up, or for ad-hoc single-debtor
lookups. It's an ASP.NET WebForms page, so it goes through
ucc/aspnet_form.py's viewstate handshake.

FIELD_MAP values below are PLACEHOLDERS — run
`scripts/discover_form_fields.py https://web.sos.ky.gov/ftucc/search.aspx`
locally and replace them with the real field names before this will work.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Optional

import requests

from .base import UCCFiling, UCCSource, IngestMethod
from . import aspnet_form

SEARCH_URL = "https://web.sos.ky.gov/ftucc/search.aspx"

# Verified 2026-06-17 via discover_form_fields.py against live search page
FIELD_MAP = {
    "org_name": "ctl00$ContentPlaceHolder1$SearchForm1$tOrgname",
    "submit_button": "ctl00$ContentPlaceHolder1$SearchForm1$bSearch",
}


class KentuckyUCCSource(UCCSource):
    state = "KY"
    ingest_method = IngestMethod.BULK  # primary path — see module docstring
    is_free = True

    def search(self, debtor_name: str) -> list[UCCFiling]:
        """Fallback live-search path. Untested against the real site from
        this sandbox (KY .gov isn't in the allowed network egress here) —
        verify field names with discover_form_fields.py before relying on
        this in production."""
        session = requests.Session()
        fields = aspnet_form.get_form_fields(session, SEARCH_URL)
        fields[FIELD_MAP["org_name"]] = debtor_name
        fields[FIELD_MAP["submit_button"]] = "Search"

        response = aspnet_form.post_form(session, SEARCH_URL, fields)
        return self._parse_search_results(response.text, debtor_name)

    def _parse_search_results(self, html: str, debtor_name: str) -> list[UCCFiling]:
        """Placeholder parser — needs real HTML structure once the search
        actually works. KY results are typically a table with columns:
        Filing Number | Filing Date | Lapse Date | Debtor | Secured Party."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        filings = []
        # NOTE: table selector below is a guess (e.g. id="gvResults") —
        # confirm against real page HTML once reachable.
        results_table = soup.find("table", id="gvResults")
        if not results_table:
            return filings

        for row in results_table.find_all("tr")[1:]:  # skip header
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 4:
                continue
            filing_number, filing_date_str, debtor, secured_party = cells[:4]
            filings.append(
                UCCFiling(
                    state=self.state,
                    debtor_name=debtor,
                    secured_party_name=secured_party,
                    filing_number=filing_number,
                    filing_date=_parse_date(filing_date_str),
                    source_url=SEARCH_URL,
                    raw={"cells": cells},
                )
            )
        return filings

    def bulk_ingest(self, file_path: str) -> list[UCCFiling]:
        """Parse a downloaded KY Bulk Data Service file. Assumes CSV with
        a header row — ADJUST once you see the real file format/columns,
        which weren't documented publicly. Likely column names based on
        the bulk service's stated contents (filings + collateral +
        secured parties/debtors as separate files, per the SOS page) —
        this assumes a joined/flat export; may need restructuring if KY
        ships normalized multi-file output instead."""
        filings = []
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                filings.append(
                    UCCFiling(
                        state=self.state,
                        debtor_name=row.get("DEBTOR_NAME", "").strip(),
                        secured_party_name=row.get("SECURED_PARTY_NAME", "").strip(),
                        filing_number=row.get("FILING_NUMBER", "").strip(),
                        filing_date=_parse_date(row.get("FILING_DATE", "")),
                        lapse_date=_parse_date(row.get("LAPSE_DATE", "")),
                        filing_type=row.get("FILING_TYPE", "UCC-1").strip(),
                        collateral_description=row.get("COLLATERAL_DESC", "").strip() or None,
                        status=row.get("STATUS", "active").strip().lower(),
                        source_url="ky_bulk_data_service",
                        raw=dict(row),
                    )
                )
        return filings


def _parse_date(value: str) -> Optional[object]:
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None
