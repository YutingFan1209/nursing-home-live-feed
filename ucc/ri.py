"""
ucc/ri.py

Rhode Island UCC lender search (added 2026-10-21) on the Department of
State's public search, business.sos.ri.gov/CorpWeb/UCCSearch. No captcha or
bot wall, and no robots.txt on the host (checked 2026-10-21).

The search takes a secured-party name ("Begins With", organization) as plain
GET parameters and lists every filing in the chain (UCC-1, then each UCC-3)
with its date, but not the debtor. The debtor is on the filing history page
(UCCFilingHistory.aspx?UCC1=<id>), which only answers inside a session that
ran a search and with the results page as referrer; otherwise it shows the
blank search form.

So per lender term: one results request, keep the UCC-1s from that lender
within RECENT_DAYS, then one history request per UCC-1 for its debtors.
First find: Capital Funding's UCC-1 on AdviniaCare Pawtucket Pleasant Rehab
Center, 10/13/2026, replacing Capital Finance's of 11/17/2025.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from ucc.base import UCCFiling
from ucc.lender_search import RECENT_DAYS
from ucc.lien_portal import lender_matches

logger = logging.getLogger(__name__)

BASE = "https://business.sos.ri.gov/CorpWeb/UCCSearch/"
GAP = 5  # seconds between requests
HEADERS = {"User-Agent": "Mozilla/5.0 (nursing-home-live-feed UCC lender search)"}
LAST_SEARCHED: dict[str, int] = {}  # terms searched in the last run, for main.py's resume offset


def _date(s: str):
    s = (s or "").strip().split(" ")[0]
    try:
        return datetime.strptime(s, "%m/%d/%Y").date()
    except ValueError:
        return None


def results_url(name: str) -> str:
    # every key the search form sends, even blank ones: the results page is
    # empty if any is missing
    return BASE + "UCCSearchResults.aspx?" + requests.compat.urlencode({
        "SearchLapsed": "False", "rdoSearch": "O", "txtLast": "", "txtFirst": "", "txtMiddle": "",
        "txtSuffix": "", "txtICity": "", "txtIState": "", "txtName": name, "txtOCity": "", "txtOState": "",
        "txtFilingNumber": "", "txtStartDate": "", "UCCSearchMethod": "B", "chkDebtor": "",
        "chkSecuredParty": "S", "chkAssignee": "", "lstDisplay": "10000"})


def parse_results(html: str) -> list[dict]:
    """Rows of the results grid: name, name type, filing type, filing
    number, original filing number, filing date and the UCC1 history id."""
    rows = []
    for tr in BeautifulSoup(html, "html.parser").select("#MainContent_grdSearchResults tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.select("td")]
        link = tr.select_one("a[href*='UCC1=']")
        if len(cells) < 8 or not link:
            continue
        rows.append({"name": cells[0], "name_type": cells[1], "filing_type": cells[4],
                     "filing_number": cells[5], "original": cells[6], "date": _date(cells[7]),
                     "ucc1": re.search(r"UCC1=(\d+)", link["href"]).group(1)})
    return rows


def parse_history(html: str) -> dict:
    """The initial filing's debtors, plus whether a later UCC-3 terminated it."""
    table = BeautifulSoup(html, "html.parser").select_one("#MainContent_tblFilingHistory")
    if not table:
        return {}
    debtors, section, sections = [], None, 0
    for tr in table.select("tr"):
        tds = tr.select("td")
        text = tr.get_text(" ", strip=True)
        if len(tds) == 1 and tds[0].get("align") == "center":
            sections += 1                       # "UCC-1 Standard", "UCC-3 AMENDMENT", ...
            section = None
            continue
        if text in ("Debtor(s)", "Secured Parties", "Collateral Information"):
            section = text
            continue
        if sections == 1 and section == "Debtor(s)":
            for td in tds:
                lines = [l.strip() for l in td.get_text("\n").split("\n") if l.strip()]
                if lines:
                    debtors.append({"name": lines[0], "address": ", ".join(lines[1:])})
    return {"debtors": debtors, "terminated": "UCC-3 TERMINATION" in table.get_text(" ").upper()}


def search_secured_party(name: str, session: requests.Session = None, today: date = None,
                         pause=time.sleep) -> list[UCCFiling]:
    """This lender's UCC-1s from the last RECENT_DAYS, one per filing, with
    the first debtor as debtor_name and any others as co_debtors."""
    session = session or requests.Session()
    since = (today or date.today()) - timedelta(days=RECENT_DAYS)
    url = results_url(name)
    r = session.get(url, headers=HEADERS, timeout=90)
    r.raise_for_status()
    rows = parse_results(r.text)
    terminated = {row["original"] for row in rows if "TERMINATION" in row["filing_type"].upper()}
    initial = {row["ucc1"]: row for row in rows
               if row["name_type"] == "SECURED PARTY" and row["filing_type"].upper().startswith("UCC-1")
               and row["date"] and row["date"] >= since and lender_matches(name, row["name"])}
    filings = []
    for ucc1, row in initial.items():
        pause(GAP)
        h = session.get(BASE + f"UCCFilingHistory.aspx?SearchLapsed=False&UCC1={ucc1}",
                        headers={**HEADERS, "Referer": url}, timeout=90)
        h.raise_for_status()
        history = parse_history(h.text)
        debtors = history.get("debtors") or []
        if not debtors:
            logger.warning("RI UCC %s: no debtor on filing history %s", row["filing_number"], ucc1)
            continue
        filings.append(UCCFiling(
            state="RI",
            debtor_name=debtors[0]["name"],
            secured_party_name=row["name"],
            filing_number=row["filing_number"],
            filing_date=row["date"],
            lapse_date=row["date"] + timedelta(days=5 * 365 + 1),  # UCC-1s run 5 years
            filing_type="UCC-1",
            status="terminated" if history.get("terminated") or row["filing_number"] in terminated else "active",
            source_url=BASE + "UCCSearch.aspx",  # the history page needs a search session
            raw={"address": debtors[0]["address"], "query_name": name, "ucc1_id": ucc1},
            co_debtors=[d["name"] for d in debtors[1:]],
        ))
    return filings


def search_lenders(terms: list[str]) -> list[UCCFiling]:
    """Every recent UCC-1 for each lender term, GAP apart. A failed term is
    logged and skipped; a refusal (403/429) ends the run, keeping what was
    found so far."""
    from pipeline.run_health import health
    filings, session = [], requests.Session()
    LAST_SEARCHED["RI"] = len(terms)
    for i, term in enumerate(terms):
        if i:
            time.sleep(GAP)
        try:
            hits = search_secured_party(term, session)
            logger.info("RI UCC lender %s -> %d filings", term, len(hits))
            filings.extend(hits)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (403, 429):
                logger.warning("RI UCC: portal refused at %r (%d of %d searched); stopping", term, i, len(terms))
                health.failed("UCC RI lender searches", f"refused at {term} ({i} of {len(terms)})")
                LAST_SEARCHED["RI"] = i
                break
            logger.warning("RI UCC lender search failed for %r: %s", term, e)
            health.failed("UCC RI lender searches", f"{term}: {e}")
        except Exception as e:
            logger.warning("RI UCC lender search failed for %r: %s", term, e)
            health.failed("UCC RI lender searches", f"{term}: {e}")
    return filings
