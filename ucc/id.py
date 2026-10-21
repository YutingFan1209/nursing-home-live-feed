"""
ucc/id.py

Idaho UCC debtor search (added 2026-10-21) on the Secretary of State's
sosbiz.idaho.gov, the same software as Pennsylvania's file.dos.pa.gov but
an older search API: Idaho's has no secured-party mode, so unlike AR/NM/RI
it searches nursing home entity names (CHOW buyers, CMS organization
owners), not lenders.

`POST /api/Records/uccsearch` with {SEARCH_VALUE, STARTS_WITH_YN, STATUS,
FILING_DATE, LAPSE_DATE, CITY} -- the body the page itself sends; the PA-style
body (SEARCH_TYPE, ORGANIZATION_NAME) gets a 500. The page sends
"Authorization: undefined" for a visitor who isn't logged in. Each row has
debtor ("NAME - CITY, ST"), secured party, file number, type (UCC or State
Lien), status, filing and lapse dates. No captcha or bot wall; robots.txt
only closes /api/reports/GetImageByNum/ (checked 2026-10-20/21).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import requests

from ucc.base import UCCFiling

logger = logging.getLogger(__name__)

HOST = "https://sosbiz.idaho.gov"
SEARCH_URL = HOST + "/api/Records/uccsearch"
GAP = 5  # seconds between searches
HEADERS = {"User-Agent": "Mozilla/5.0 (nursing-home-live-feed UCC debtor search)", "Authorization": "undefined"}
LAST_SEARCHED: dict[str, int] = {}

# CMS owners that aren't nursing home operators or landlords: a county or
# hospital district's loans finance the hospital, not the home
NOT_A_HOME_OWNER = re.compile(r"\b(COUNTY|HOSPITAL|DISTRICT|CITY OF|TRUST|MEMORIAL|MEDICAL CENTER)\b", re.I)


def _date(s: str):
    try:
        return datetime.strptime((s or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def _split_party(s: str) -> tuple[str, str]:
    """'BOISE SNF OPERATIONS LLC - BOISE, ID' -> (name, 'BOISE, ID')."""
    name, sep, place = (s or "").rpartition(" - ")
    return (name, place) if sep else (s or "", "")


def rows_to_filings(rows: dict, query: str) -> list[UCCFiling]:
    """UCC rows only (State Lien rows are tax liens); a filing with several
    debtors or secured parties lists them all, the first is used."""
    filings = []
    for row in (rows or {}).values():
        if (row.get("RECORD_TYPE") or "").upper() != "UCC":
            continue
        debtors = [_split_party(t) for t in row.get("TITLE") or []]
        lenders = [_split_party(t) for t in row.get("SEC_PARTY") or []]
        if not debtors:
            continue
        status = (row.get("STATUS") or "unknown").lower()
        filings.append(UCCFiling(
            state="ID",
            debtor_name=debtors[0][0],
            secured_party_name=lenders[0][0] if lenders else "",
            filing_number=str(row.get("RECORD_NUM") or ""),
            filing_date=_date(row.get("FILING_DATE")),
            lapse_date=_date(row.get("LAPSE_DATE")),
            filing_type="UCC-1",
            status="terminated" if status in ("released", "terminated") else status,
            source_url=HOST + "/search/ucc",
            raw={"address": debtors[0][1], "sp_address": lenders[0][1] if lenders else "",
                 "query_name": query, "record_id": row.get("ID")},
            co_debtors=[d[0] for d in debtors[1:]],
        ))
    return filings


def search_debtor(name: str, session: requests.Session = None) -> list[UCCFiling]:
    session = session or requests.Session()
    body = {"SEARCH_VALUE": name, "STARTS_WITH_YN": True, "STATUS": "ALL", "EXCLUDE_VALUES": "",
            "FILING_DATE": {"start": None, "end": None}, "LAPSE_DATE": {"start": None, "end": None}, "CITY": ""}
    r = session.post(SEARCH_URL, json=body, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return rows_to_filings(r.json().get("rows") or {}, name)


def search_debtors(names: list[str]) -> list[UCCFiling]:
    """Every UCC filing for each name, GAP apart. A failed name is logged
    and skipped; a refusal (403/429) ends the run, keeping what was found."""
    from pipeline.run_health import health
    filings, session = [], requests.Session()
    LAST_SEARCHED["ID"] = len(names)
    for i, name in enumerate(names):
        if i:
            time.sleep(GAP)
        try:
            hits = search_debtor(name, session)
            logger.info("ID UCC debtor %s -> %d filings", name, len(hits))
            filings.extend(hits)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (403, 429):
                logger.warning("ID UCC: portal refused at %r (%d of %d searched); stopping", name, i, len(names))
                health.failed("UCC ID debtor searches", f"refused at {name} ({i} of {len(names)})")
                LAST_SEARCHED["ID"] = i
                break
            logger.warning("ID UCC debtor search failed for %r: %s", name, e)
            health.failed("UCC ID debtor searches", f"{name}: {e}")
        except Exception as e:
            logger.warning("ID UCC debtor search failed for %r: %s", name, e)
            health.failed("UCC ID debtor searches", f"{name}: {e}")
    return filings
