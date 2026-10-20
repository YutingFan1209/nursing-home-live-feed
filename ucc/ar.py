"""
ucc/ar.py

Arkansas UCC lender search (bcs.sos.arkansas.gov, added 2026-10-20).

The Secretary of State's free lien search has a Secured Party Name Search
whose JSON answer lists debtor, secured party, status, filing and lapse
dates together, so one request per lender is enough (Arizona's lists only
the lender and needs a second lookup per filing). No captcha or bot wall,
no robots.txt on bcs.sos.arkansas.gov, and the acceptable-use policy says
nothing on automation (checked 2026-10-20). sos.arkansas.gov's robots.txt
asks for a 5 s crawl delay, which REQUEST_GAP_S keeps.

Results go through ucc/lender_search.py's filter_lender_hits: recent
filings whose debtor looks like a nursing home, plus same-day filings from
the same lender (a home's realty company filed alongside its opco: Bryant
Operating SNF + Bryant Realty SNF, Capital Funding, 2026-08-10/11).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import requests

from ucc.base import UCCFiling

logger = logging.getLogger(__name__)

SEARCH_URL = "https://bcs.sos.arkansas.gov/api/uccEntitySearch/webSearch"
PORTAL_URL = "https://bcs.sos.arkansas.gov/search/ucc"
REQUEST_GAP_S = 5
HEADERS = {"User-Agent": "Mozilla/5.0 (nursing-home-live-feed UCC lender search)"}


def _date(s: str):
    try:
        return datetime.strptime((s or "").strip(), "%m/%d/%Y %I:%M %p").date()
    except ValueError:
        return None


def _split_party(s: str) -> tuple[str, str]:
    """'BRYANT OPERATING SNF LLC - ROSENBERG, TX' -> (name, 'ROSENBERG, TX')."""
    name, sep, place = (s or "").rpartition(" - ")
    return (name, place) if sep else (s or "", "")


def rows_to_filings(rows: dict, query: str) -> list[UCCFiling]:
    filings = []
    for row in (rows or {}).values():
        debtor, debtor_place = _split_party(row.get("DEBTOR"))
        lender, lender_place = _split_party(row.get("SEC_PARTY"))
        filings.append(UCCFiling(
            state="AR",
            debtor_name=debtor,
            secured_party_name=lender,
            filing_number=str(row.get("RECORD_NUM") or ""),
            filing_date=_date(row.get("FILING_DATE")),
            lapse_date=_date(row.get("LAPSE_DATE")),
            filing_type=row.get("RECORD_TYPE") or "UCC-1",
            status=(row.get("STATUS") or "unknown").lower(),
            source_url=PORTAL_URL,
            raw={"address": debtor_place, "sp_address": lender_place, "query_name": query,
                 "ucc_record_id": row.get("UCC_RECORD_ID")},
        ))
    return filings


def search_secured_party(name: str, session: requests.Session = None) -> list[UCCFiling]:
    params = {"SEARCH_VALUE": name, "SearchType": "SecuredParty", "NameTypeId": 2,
              "OrganizationName": name, "SearchCity": "", "SearchState": "", "SearchLapsed": "false"}
    r = (session or requests).get(SEARCH_URL, params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return rows_to_filings(r.json().get("rows"), name)


def search_ar_lenders(terms: list[str]) -> list[UCCFiling]:
    """Every filing for each lender term, REQUEST_GAP_S apart. A failed term
    is logged and skipped; the rest still run."""
    from pipeline.run_health import health
    filings, session = [], requests.Session()
    for i, term in enumerate(terms):
        if i:
            time.sleep(REQUEST_GAP_S)
        try:
            hits = search_secured_party(term, session)
            logger.info("AR UCC lender %s -> %d filings", term, len(hits))
            filings.extend(hits)
        except Exception as e:
            logger.warning("AR UCC lender search failed for %r: %s", term, e)
            health.failed("UCC AR lender searches", f"{term}: {e}")
    return filings
