"""
ucc/lien_portal.py

UCC lender search for states whose Secretary of State runs the same lien
search platform: Arkansas (bcs.sos.arkansas.gov, added 2026-10-20) and New
Mexico (enterprise.sos.nm.gov, same day). Idaho's sosbiz looks alike but
searches debtor names only and needs a login token, so it isn't here.

The platform's Secured Party Name Search answers in JSON with debtor,
secured party, status, filing and lapse dates together, so one request per
lender and date window is enough (Arizona's lists only the lender and needs
a second lookup per filing). No captcha or bot wall, no robots.txt on either
search host, and neither state's site terms say anything on automation
(checked 2026-10-20). sos.arkansas.gov's robots.txt asks for a 5 s crawl
delay, which STATES[...]["gap"] keeps (NM runs slower, see STATES).

Two portal quirks:
- At most 100 rows come back. Each search covers only the last
  RECENT_DAYS (the same window filter_lender_hits keeps), and a window that
  comes back full is split in half and searched again.
- New Mexico matches any of the words ("Capital Funding" also returns Cloud
  Capital Funding, Express Capital Funding), so for STATES[...]["loose"]
  only rows whose secured party contains every word of the term are kept.

Results go through ucc/lender_search.py's filter_lender_hits: recent
filings whose debtor looks like a nursing home, plus same-day filings from
the same lender (a home's realty company filed alongside its opco: Bryant
Operating SNF + Bryant Realty SNF, Capital Funding, 2026-08-10/11).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta

import requests

from ucc.base import UCCFiling
from ucc.lender_search import RECENT_DAYS

logger = logging.getLogger(__name__)

# gap: seconds between requests. NM answered 403 to every search after
# about 20 requests in 15 minutes at 5 s (2026-10-20), so it runs slower and
# a run stops at the first refusal rather than pressing on
STATES = {
    "AR": {"host": "https://bcs.sos.arkansas.gov", "loose": False, "gap": 5},
    "NM": {"host": "https://enterprise.sos.nm.gov", "loose": True, "gap": 30},
}
SEARCH_PATH = "/api/uccEntitySearch/webSearch"
MAX_ROWS = 100
MIN_WINDOW_DAYS = 7
LAST_SEARCHED: dict[str, int] = {}  # terms searched in each state's last run, for main.py's resume offset
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


def _words(s: str) -> list[str]:
    return re.sub(r"[^A-Z0-9 ]+", " ", (s or "").upper().replace("&", " AND ")).split()


def lender_matches(term: str, secured_party: str) -> bool:
    """The secured party starts with the term's first word and has every
    word of it: "CAPITAL FUNDING, LLC" for "Capital Funding", not "EXPRESS
    CAPITAL FUNDING"; "SECRETARY OF U.S. DEPARTMENT OF HOUSING & URBAN
    DEVELOPMENT" for "SECRETARY OF HOUSING AND URBAN DEVELOPMENT"."""
    t, p = _words(term), _words(secured_party)
    return bool(t and p) and t[0] == p[0] and set(t) <= set(p)


def rows_to_filings(rows: dict, query: str, state: str = "AR") -> list[UCCFiling]:
    filings = []
    for row in (rows or {}).values():
        debtor, debtor_place = _split_party(row.get("DEBTOR"))
        lender, lender_place = _split_party(row.get("SEC_PARTY"))
        filings.append(UCCFiling(
            state=state,
            debtor_name=debtor,
            secured_party_name=lender,
            filing_number=str(row.get("RECORD_NUM") or ""),
            filing_date=_date(row.get("FILING_DATE")),
            lapse_date=_date(row.get("LAPSE_DATE")),
            filing_type=row.get("RECORD_TYPE") or "UCC-1",
            status=(row.get("STATUS") or "unknown").lower(),
            source_url=STATES[state]["host"] + "/search/ucc",
            raw={"address": debtor_place, "sp_address": lender_place, "query_name": query,
                 "ucc_record_id": row.get("UCC_RECORD_ID")},
        ))
    return filings


def _search_window(state: str, name: str, start: date, end: date, session) -> dict:
    params = {"SEARCH_VALUE": name, "SearchType": "SecuredParty", "NameTypeId": 2,
              "OrganizationName": name, "SearchCity": "", "SearchState": "",
              "startDate": start.strftime("%m/%d/%Y"), "endDate": end.strftime("%m/%d/%Y"),
              "SearchLapsed": "false"}
    r = session.get(STATES[state]["host"] + SEARCH_PATH, params=params, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json().get("rows") or {}


def search_secured_party(state: str, name: str, session: requests.Session = None,
                         today: date = None, pause=time.sleep) -> list[UCCFiling]:
    """Filings for one lender over the last RECENT_DAYS, splitting any window
    that hits the portal's 100-row cap."""
    session = session or requests.Session()
    end = today or date.today()
    windows, rows, first = [(end - timedelta(days=RECENT_DAYS), end)], {}, True
    while windows:
        start, stop = windows.pop()
        if not first:
            pause(STATES[state]["gap"])
        first = False
        got = _search_window(state, name, start, stop, session)
        if len(got) >= MAX_ROWS and (stop - start).days > MIN_WINDOW_DAYS:
            mid = start + (stop - start) / 2
            windows += [(start, mid), (mid + timedelta(days=1), stop)]
            continue
        if len(got) >= MAX_ROWS:
            logger.warning("%s UCC lender %r: %s–%s still has %d+ rows", state, name, start, stop, MAX_ROWS)
        rows.update(got)
    filings = rows_to_filings(rows, name, state)
    if STATES[state]["loose"]:
        filings = [f for f in filings if lender_matches(name, f.secured_party_name)]
    return filings


def search_lenders(state: str, terms: list[str]) -> list[UCCFiling]:
    """Every recent filing for each lender term, STATES[state]["gap"] apart.
    A failed term is logged and skipped; a refusal (403/429) ends the run,
    keeping what was found so far."""
    from pipeline.run_health import health
    filings, session = [], requests.Session()
    LAST_SEARCHED[state] = len(terms)
    for i, term in enumerate(terms):
        if i:
            time.sleep(STATES[state]["gap"])
        try:
            hits = search_secured_party(state, term, session)
            logger.info("%s UCC lender %s -> %d filings", state, term, len(hits))
            filings.extend(hits)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (403, 429):
                logger.warning("%s UCC: portal refused at %r (%d of %d searched); stopping",
                               state, term, i, len(terms))
                health.failed(f"UCC {state} lender searches", f"refused at {term} ({i} of {len(terms)})")
                LAST_SEARCHED[state] = i
                break
            logger.warning("%s UCC lender search failed for %r: %s", state, term, e)
            health.failed(f"UCC {state} lender searches", f"{term}: {e}")
        except Exception as e:
            logger.warning("%s UCC lender search failed for %r: %s", state, term, e)
            health.failed(f"UCC {state} lender searches", f"{term}: {e}")
    return filings
