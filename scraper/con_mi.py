"""
Michigan MDHHS Certificate of Need "Activity Reports" -- letters of intent.

MCL 333.22209 requires CON review to acquire an existing nursing home, and
MDHHS posts monthly activity reports on a page per year: LOIs received,
applications, and decisions. The LOI is the earliest filing, so only the
LOI reports are read. Format changed in March 2026: XLSX from then on,
PDF before. See docs/con-feasibility.md (Michigan section).

Rows: received date, CON ID, Facility ID, facility, city, county, project
description, cost. Nursing homes are the "NN-4xxx" facility IDs (hospitals
are NN-0xxx, freestanding surgery NN-6xxx, mobile equipment NN-Cxxx).
The description is the only detail, abbreviated:
  "ACQ 39 BED NH BY MI CH OPCO LLC [10-YR LEASE]"      -> ownership change
  "MEMBERSHIP INT TFER & BUILDING PURCHASE (NO LEASE)"  -> ownership change
  "NEW 36-MTH LEASE & NEW LANDLORD [WAIVER]"            -> kept (property sold)
  "INDIRECT OWNERSHIP CHG & BOND PAYOFF"                -> kept
  "NEW LEASE [35 YEARS]"                                -> kept; may be a
                                                           propco re-lease
  "NEW NH W/20 BEDS [PA-50][10-YR LEASE]"               -> skipped (construction)
  "RENEW NH LEASE [25-YRS]", "RELOCATE 8 NH BEDS ..."   -> skipped
The buyer is taken from the "BY ..." clause when there is one.

PDF-era rows have no column separators and wrap across lines. They're
split on the county (Michigan's 83 counties are a fixed list), and the
facility name is separated from the city using the cities CMS lists for
Michigan nursing homes; the caller passes those in.
"""

import html
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import openpyxl
from curl_cffi import requests as cffi_requests
from pypdf import PdfReader

from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_MI_SOURCE_NAME = "Michigan MDHHS Certificate of Need Activity Reports"
CON_MI_INDEX_URL = "https://www.michigan.gov/mdhhs/doing-business/providers/certificateofneed/reports/activity-reports"
CON_MI_YEAR_URL = "https://www.michigan.gov/mdhhs/doing-business/providers/certificateofneed/program/archive/{year}-activity-reports"
CON_MI_RECENCY_DAYS = 365

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}
_LINK_RE = re.compile(r'href="([^"]+\.(?:xlsx|pdf)[^"]*)"[^>]*>(.*?)</a>', re.S)
_NURSING_HOME_ID_RE = re.compile(r"^\d{2}-4\d{3}$")
_OWNERSHIP_RE = re.compile(
    r"\bACQ|ACQUI|\bTFER\b|TRANSFER|PURCHASE|OWNERSHIP|\bMEMB|LANDLORD"
    r"|\bNEW\b[^\[]*?\bLEASE|CHANGE OF OWN|\bCHOW\b", re.I)
# "NEW NH W/20 BEDS [...][10-YR LEASE]" is new construction, not a sale
_NOT_OWNERSHIP_RE = re.compile(r"\bRENEW|\bRELOC|\bADD \d+|\bREPL|\bNEW (?:\d+[- ]BED )?NH\b", re.I)
_BUYER_RE = re.compile(r"\bBY (.+?)\s*(?:\[|\(|$)", re.I)

COUNTIES = [
    "ALCONA", "ALGER", "ALLEGAN", "ALPENA", "ANTRIM", "ARENAC", "BARAGA", "BARRY", "BAY", "BENZIE",
    "BERRIEN", "BRANCH", "CALHOUN", "CASS", "CHARLEVOIX", "CHEBOYGAN", "CHIPPEWA", "CLARE", "CLINTON",
    "CRAWFORD", "DELTA", "DICKINSON", "EATON", "EMMET", "GENESEE", "GLADWIN", "GOGEBIC",
    "GRAND TRAVERSE", "GRATIOT", "HILLSDALE", "HOUGHTON", "HURON", "INGHAM", "IONIA", "IOSCO", "IRON",
    "ISABELLA", "JACKSON", "KALAMAZOO", "KALKASKA", "KENT", "KEWEENAW", "LAKE", "LAPEER", "LEELANAU",
    "LENAWEE", "LIVINGSTON", "LUCE", "MACKINAC", "MACOMB", "MANISTEE", "MARQUETTE", "MASON", "MECOSTA",
    "MENOMINEE", "MIDLAND", "MISSAUKEE", "MONROE", "MONTCALM", "MONTMORENCY", "MUSKEGON", "NEWAYGO",
    "OAKLAND", "OCEANA", "OGEMAW", "ONTONAGON", "OSCEOLA", "OSCODA", "OTSEGO", "OTTAWA",
    "PRESQUE ISLE", "ROSCOMMON", "SAGINAW", "ST CLAIR", "ST JOSEPH", "SANILAC", "SCHOOLCRAFT",
    "SHIAWASSEE", "TUSCOLA", "VAN BUREN", "WASHTENAW", "WAYNE", "WEXFORD",
]
# Longest first so "ST JOSEPH" wins over a city that happens to end in "JOSEPH"
_COUNTY_RE = re.compile(r"\b(" + "|".join(sorted(COUNTIES, key=len, reverse=True)) + r")\b")
_PDF_ROW_RE = re.compile(r"^(\d{2}/\d{2}/\d{4}) (\d{2}-\d{4}) (\S+) ", re.M)


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def _loi_reports(year: int) -> list[tuple[date, str]]:
    """(report month, absolute URL) for the year's LOI reports."""
    page = _get(CON_MI_YEAR_URL.format(year=year)).text
    reports = []
    for href, label in _LINK_RE.findall(page):
        href = html.unescape(href)
        month = _MONTHS.get(re.sub(r"<[^>]+>|\s+", "", label).lower())
        if month and re.search(r"LOI", href.split("?")[0].rsplit("/", 1)[-1], re.I):
            reports.append((date(year, month, 1), urljoin(CON_MI_INDEX_URL, href)))
    return reports


def _rows_from_xlsx(content: bytes) -> list[dict]:
    ws = openpyxl.load_workbook(io.BytesIO(content), read_only=True).worksheets[0]
    rows = []
    for r in ws.iter_rows(values_only=True):
        if not r or not isinstance(r[1], str) or not re.match(r"\d{2}-\d{4}$", r[1].strip()):
            continue
        received = r[0] if isinstance(r[0], (datetime, date)) else datetime.strptime(str(r[0]).strip(), "%m/%d/%Y")
        rows.append({
            "received": received.date() if isinstance(received, datetime) else received,
            "con_id": r[1].strip(), "facility_id": str(r[2]).strip(),
            "facility": str(r[3] or "").strip(), "city": str(r[4] or "").strip(),
            "county": str(r[5] or "").strip(), "description": str(r[6] or "").strip(),
        })
    return rows


def _split_name_city(before_county: str, cities: set[str]) -> tuple[str, str]:
    words = before_county.split()
    for n in (3, 2, 1):  # longest city match first ("BATTLE CREEK", "ST CLAIR SHORES")
        if len(words) > n and " ".join(words[-n:]) in cities:
            return " ".join(words[:-n]), " ".join(words[-n:])
    return before_county, ""


def _rows_from_pdf(content: bytes, cities: set[str]) -> list[dict]:
    text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(content)).pages)
    starts = list(_PDF_ROW_RE.finditer(text))
    rows = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        # Costs are printed in a separate block after the rows; drop anything from "$" or "N of M" on
        body = re.split(r"\n\d+ of \d+\n|\n\$", text[m.end():end])[0]
        body = re.sub(r"\s+", " ", body).strip()
        county = None
        for c in _COUNTY_RE.finditer(body):
            county = c  # last county before the description wins
            if not _COUNTY_RE.search(body[c.end():c.end() + 30]):
                break
        if not county:
            continue
        name, city = _split_name_city(body[:county.start()].strip(), cities)
        rows.append({
            "received": datetime.strptime(m.group(1), "%m/%d/%Y").date(),
            "con_id": m.group(2), "facility_id": m.group(3),
            "facility": name, "city": city, "county": county.group(1),
            "description": body[county.end():].strip(),
        })
    return rows


def is_ownership_change(row: dict) -> bool:
    if not _NURSING_HOME_ID_RE.match(row["facility_id"]):
        return False
    desc = row["description"]
    return bool(_OWNERSHIP_RE.search(desc)) and not _NOT_OWNERSHIP_RE.search(desc)


def record_url(con_id: str) -> str:
    return f"{CON_MI_INDEX_URL}#con-{con_id}"


def fetch_con_mi_deals(is_known, nursing_home_cities: set[str]) -> list[dict]:
    """
    Pre-extracted deal dicts for nursing home ownership-change LOIs not yet
    stored. nursing_home_cities: upper-case Michigan cities from CMS, used to
    split facility name from city in the pre-2026-03 PDF reports.
    """
    today = date.today()
    reports = []
    for year in (today.year, today.year - 1):
        try:
            reports += _loi_reports(year)
        except Exception as e:
            if year == today.year:
                logger.error(f"MI CON {year} report page fetch failed: {e}")
                health.source_failed("MI CON activity reports", f"{year} page: {e}")
                return []
            logger.warning(f"MI CON {year} report page fetch failed: {e}")
    if not reports:
        health.source_failed("MI CON activity reports", "0 LOI reports listed (layout change?)")
        return []

    cutoff = today - timedelta(days=CON_MI_RECENCY_DAYS)
    deals, seen = [], set()
    for month, url in sorted(reports, reverse=True):
        if month < cutoff.replace(day=1):
            continue
        health.attempted("MI CON report parse")
        try:
            content = _get(url).content
            is_xlsx = url.split("?")[0].lower().endswith(".xlsx")
            rows = _rows_from_xlsx(content) if is_xlsx else _rows_from_pdf(content, nursing_home_cities)
        except Exception as e:
            logger.warning(f"MI CON {month:%B %Y} LOI report failed: {e}")
            health.failed("MI CON report parse", f"{url}: {e}")
            continue
        if not rows:
            health.failed("MI CON report parse", f"{url}: 0 rows parsed")
            continue

        for row in rows:
            if row["con_id"] in seen or not is_ownership_change(row) or row["received"] < cutoff:
                continue
            seen.add(row["con_id"])
            if is_known(record_url(row["con_id"])):
                continue

            buyer = _BUYER_RE.search(row["description"])
            buyer = buyer.group(1).strip() if buyer else None
            where = ", ".join(p for p in (row["city"].title(), f"{row['county'].title()} County") if p)
            summary = (
                f"Michigan CON letter of intent {row['con_id']}, received "
                f"{row['received']:%m/%d/%Y}: {row['facility']} ({where}), nursing home "
                f"facility ID {row['facility_id']}. Project: {row['description']}."
                + ("" if buyer else " MDHHS does not publish the buyer.")
            )
            deals.append({
                "url": record_url(row["con_id"]),
                "title": f"[MI CON] {row['con_id']} {row['description']}: {row['facility'].title()}",
                "published_at": datetime.combine(row["received"], datetime.min.time()).replace(tzinfo=timezone.utc),
                "raw_text": f"{summary}\n\nSource: {url}",
                "source_type": "con",
                "pre_extracted": True,
                "extraction_model": "con_direct",
                "_con_id": row["con_id"],
                "acquiring_entity": buyer,
                "seller_entity": None,
                "operator_names": [buyer] if buyer else [],
                "facility_names": [row["facility"].title()],
                "states": ["MI"],
                "facility_count": 1,
                "deal_value_m": None,
                # LOI date -- the closing date isn't published
                "acquisition_date": row["received"].isoformat(),
                "financing_amount_m": None,
                "lender": None,
                "rationale": summary,
            })

    logger.info(f"MI CON: {len(deals)} new nursing home ownership-change LOIs")
    return deals
