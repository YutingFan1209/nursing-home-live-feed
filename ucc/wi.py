"""
ucc/wi.py

Wisconsin UCC debtor search (added 2026-10-27) on DFI's wims.dfi.wi.gov.
The public search puts a reCAPTCHA on every search; logged in (a free
account) it doesn't, so this runs only in the person's own Chrome
(scripts/wi_ucc_browser.py). No robots.txt; the portal states no terms
on automated searching (checked 2026-10-27).

The page's own request is `POST wimsapi.dfi.wi.gov/api/uccsearchquery/
getuccsearchresults` with the account's bearer token; the body takes
searchType "debtor" and searchMethod "startsWith". Debtor search only: the
page offers no secured-party search. Each row has the lien number, filing
type, filing and lapse dates, status, debtor and the secured party's name.
DFI also sells the data ($250 a weekly file, $500 a monthly refresh).
"""
from __future__ import annotations

import re
from datetime import datetime

from ucc.base import UCCFiling

SEARCH_PAGE = "https://wims.dfi.wi.gov/uccsearch"

# "Starts with" needs the name as filed: "ARIA AT VILLA PINES, LLC" misses a
# filing under "ARIA AT VILLA PINES LLC", so the entity suffix is dropped
_SUFFIX = re.compile(r"[\s,.]+(L\.?L\.?C|INC|CORP(ORATION)?|LTD|L\.?P|CO|COMPANY)\.?$", re.I)


def search_name(name: str) -> str:
    s = (name or "").strip()
    while True:
        t = _SUFFIX.sub("", s).strip(" ,.")
        if t == s:
            return s
        s = t


def _date(s: str):
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            pass
    return None


def _type(t: str) -> str:
    t = (t or "").upper()
    if "TERMINATION" in t:
        return "UCC-3 Termination"
    if t.startswith("UCC3") or "AMENDMENT" in t or "CONTINUATION" in t or "ASSIGNMENT" in t:
        return "UCC-3"
    return "UCC-1"


def rows_to_filings(rows: list, query: str) -> list[UCCFiling]:
    filings = []
    for row in rows or []:
        number = row.get("lienNumber")
        if not number or not row.get("fullName"):
            continue
        status = (row.get("lienStatus") or "unknown").lower()
        filings.append(UCCFiling(
            state="WI",
            debtor_name=row["fullName"],
            secured_party_name=row.get("securedPartyFullName") or "",
            filing_number=number,
            filing_date=_date(row.get("filingDate")),
            lapse_date=_date(row.get("lapseDate")),
            filing_type=_type(row.get("filingType")),
            status="terminated" if status in ("terminated", "released") else status,
            source_url=SEARCH_PAGE,
            raw={"query_name": query, "filing_type_text": row.get("filingType"),
                 "address": ", ".join(x for x in (row.get("city"), row.get("state")) if x),
                 "lien_guid": row.get("lienGuid")},
        ))
    return filings
