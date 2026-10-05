"""
ucc/fl_download.py

Florida UCC filings from the Florida Secured Transaction Registry's own data
downloads, not a name search. floridaucc.com (run for the Department of
State; robots.txt allows everything, and its terms of use don't restrict
automated use) publishes, through publicsearchapi.floridaucc.com/Downloads:

- Regular: one set of pipe-delimited files per business day for the last
  30 days (Filings, Debtors, Secureds, Events), ~780 UCC-1s a day statewide
- Full: zipped files of every filing on record (filings 29 MB, secureds
  111 MB zipped as of 2026-10-05)

Each call returns a signed CloudFront URL for the file. Because every
filing is in the files, this doesn't depend on knowing borrower names: a
filing is kept when a debtor looks like a nursing home entity (CMS owner or
facility name, FL CHOW buyer, FL deal name, or nursing words), plus other
filings made the same day with the same nursing home lender (a portfolio's
property companies). The equipment-lender filter in scraper/ucc.py then
applies as for every state.

Daily ingestion resumes from the last day processed (cms_load_checkpoints
key ucc_fl_last_day, YYYYMMDD); backfill_from_full() loads older filings
once from the full download.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests

from ucc.base import UCCFiling
from ucc.lender_search import LENDER_TERMS, looks_like_nursing_home, norm_name
from pipeline.run_health import health

logger = logging.getLogger(__name__)

API = "https://publicsearchapi.floridaucc.com"
PORTAL_URL = "https://floridaucc.com/search"
LAST_DAY_KEY = "ucc_fl_last_day"
REGULAR_DAYS = 30


def _signed_url(download_type: str, file_type: str, file_date: date = None) -> str | None:
    params = {"downloadType": download_type, "fileType": file_type}
    if file_date:
        params.update(fileDate=file_date.strftime("%m/%d/%Y"), utcOffset=-4)
    r = requests.get(f"{API}/Downloads", params=params, timeout=60)
    body = r.json()
    if body.get("notOk"):
        # "not available yet" for today/weekends is normal
        logger.info(f"FL UCC {file_type} {file_date}: {body.get('friendlyMessageSummary')}")
        return None
    return body["payload"]


def completed_through() -> date:
    r = requests.get(f"{API}/filings-completed-through-date", timeout=30)
    return datetime.fromisoformat(r.json()["payload"].replace("Z", "+00:00")).date()


def _rows(text_stream) -> csv.DictReader:
    return csv.DictReader(text_stream, delimiter="|")


def _parse_date(s: str):
    try:
        return datetime.strptime((s or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def _is_lender(sp: str) -> bool:
    sp = (sp or "").upper()
    return any(sp.startswith(t) for t in LENDER_TERMS)


def select_filings(filings: dict, debtors: dict, secureds: dict, names: set[str]) -> list[UCCFiling]:
    """filings: number -> Filings row; debtors/secureds: number -> list of
    names (organization debtors only). Keeps filings with a nursing home
    debtor, then same-day filings with the same nursing home lender."""
    def nursing(num):
        return any(looks_like_nursing_home(d, names, strict=True) for d in debtors.get(num, []))

    keep = {num for num in filings if nursing(num)}
    by_lender_day = defaultdict(set)
    for num, row in filings.items():
        for sp in secureds.get(num, []):
            if _is_lender(sp):
                by_lender_day[(norm_name(sp), row["FilingDate"])].add(num)
    for nums in by_lender_day.values():
        if nums & keep:
            keep |= nums

    out = []
    for num in sorted(keep):
        row = filings[num]
        debs = debtors.get(num, [])
        primary = next((d for d in debs if looks_like_nursing_home(d, names, strict=True)), debs[0] if debs else "")
        sps = secureds.get(num, [])
        out.append(UCCFiling(
            state="FL",
            debtor_name=primary,
            secured_party_name=sps[0] if sps else "",
            filing_number=num,
            filing_date=_parse_date(row["FilingDate"]),
            lapse_date=_parse_date(row.get("FilingExpDate")),
            filing_type="UCC-1",
            status="active" if (row.get("FilingStatus") or "").lower() == "filed" else (row.get("FilingStatus") or "").lower(),
            source_url=PORTAL_URL,
            co_debtors=[d for d in debs if d != primary],
            raw={"query_name": "FL daily file", "secured_parties": sps},
        ))
    return out


def _collect(filings_rows, debtor_rows, secured_rows, since: date = None):
    filings = {}
    for r in filings_rows:
        d = _parse_date(r.get("FilingDate"))
        if since and (not d or d < since):
            continue
        filings[r["Ucc1FilingNumber"]] = r
    debtors, secureds = defaultdict(list), defaultdict(list)
    for r in debtor_rows:
        num = r["Ucc1FilingNumber"]
        # P = individual; only organizations can be nursing home entities
        if num in filings and r.get("DebNameFormat") != "P" and r.get("DebName"):
            debtors[num].append(r["DebName"].strip())
    for r in secured_rows:
        num = r["Ucc1FilingNumber"]
        if num in filings and r.get("SecName"):
            secureds[num].append(r["SecName"].strip())
    return filings, debtors, secureds


def fetch_day(day: date, names: set[str]) -> list[UCCFiling] | None:
    """None if the day's files aren't published (weekend, holiday, not yet)."""
    urls = {ft: _signed_url("Regular", ft, day) for ft in ("Filings", "Debtors", "Secureds")}
    if not all(urls.values()):
        return None
    text = {ft: requests.get(u, timeout=120).text for ft, u in urls.items()}
    filings, debtors, secureds = _collect(
        _rows(io.StringIO(text["Filings"])), _rows(io.StringIO(text["Debtors"])), _rows(io.StringIO(text["Secureds"])))
    kept = select_filings(filings, debtors, secureds, names)
    logger.info(f"FL UCC {day}: {len(filings)} filings -> {len(kept)} nursing home")
    return kept


def fetch_new_days(conn, names: set[str]) -> list[UCCFiling]:
    """Every published day after the last one processed (at most the last
    30), advancing the saved day as each one is read."""
    from cms.fetch_cms import _get_checkpoint, _save_checkpoint
    last = _get_checkpoint(conn, LAST_DAY_KEY)
    through = completed_through()
    start = max(through - timedelta(days=REGULAR_DAYS - 1),
                datetime.strptime(str(last), "%Y%m%d").date() + timedelta(days=1) if last else date.min)
    out = []
    day = start
    while day <= through:
        if day.weekday() < 5:
            health.attempted("UCC FL daily files")
            try:
                kept = fetch_day(day, names)
            except Exception as e:
                health.failed("UCC FL daily files", f"{day}: {e}")
                break  # retry from this day next run
            if kept is not None:
                out.extend(kept)
        _save_checkpoint(conn, LAST_DAY_KEY, int(day.strftime("%Y%m%d")))
        conn.commit()
        day += timedelta(days=1)
    return out


def backfill_from_full(names: set[str], since: date) -> list[UCCFiling]:
    """One-off: filings dated on or after since, from the full download."""
    def zipped_rows(file_type):
        url = _signed_url("Full", file_type)
        data = requests.get(url, timeout=600).content
        zf = zipfile.ZipFile(io.BytesIO(data))
        return _rows(io.TextIOWrapper(zf.open(zf.namelist()[0]), encoding="utf-8", errors="replace"))

    filings, debtors, secureds = _collect(zipped_rows("Filings"), zipped_rows("Debtors"), zipped_rows("Secureds"), since)
    kept = select_filings(filings, debtors, secureds, names)
    logger.info(f"FL UCC backfill since {since}: {len(filings)} filings -> {len(kept)} nursing home")
    return kept
