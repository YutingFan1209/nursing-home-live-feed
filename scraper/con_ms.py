"""
Mississippi MSDH Certificate of Need Weekly Reports -- CHOW applications.

Miss. Code §41-7-191 and MSDH's CHOW rules require an application approved
by MSDH before a licensed facility changes hands. Each weekly report PDF has
a "Change of Ownership (CHOW) Applications" table: facility type, facility,
transaction (purchase / lease / donation), location, received date, and the
review deadlines. The buyer is rarely named. See docs/con-feasibility.md
(Mississippi section).

Items stay on the report only until 30 days after completion, so reading
the latest few weeks each run is enough once the backlog is in; the first
run (no MS records stored yet) reads every weekly report in the recency
window. Reports for past years live on separate archive pages.

Nursing homes are the "Nursing Home" rows, plus hospital-run long-term care
units listed under another type (e.g. "Medical Center: Bolivar Medical
Center Long Term Care").
"""

import html
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests
from pypdf import PdfReader

from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_MS_SOURCE_NAME = "Mississippi MSDH CON Weekly Reports"
CON_MS_INDEX_URL = "https://msdh.ms.gov/page/30,0,84,863.html"
CON_MS_ARCHIVE_URL = "https://msdh.ms.gov/page/30,0,84,637.html"
CON_MS_RECENCY_DAYS = 365
# Enough to cover the 30-day post-completion window with room for missed runs
RECENT_REPORTS = 6

_REPORT_LINK_RE = re.compile(r'href="([^"]+\.pdf)"[^>]*>\s*CON Weekly Report for ([A-Za-z]+ \d{1,2}, \d{4})\s*</a>')
_SECTION_START_RE = re.compile(r"Change of Ownership \(CHOW\) Applications", re.I)
_HEADER_END_RE = re.compile(r"Withdra\s*w?\s*n\s+", re.I)
_ITEM_RE = re.compile(r"Transaction:\s*(?P<tx>.*?)Location:\s*(?P<loc>.*?Mississippi)", re.S)
# The "Location:" label is sometimes missing (Tupelo Community Care Center,
# Oct 2025), which made that row swallow the next one; restore it on any
# line shaped like "City, X County, ..." before whitespace is flattened
_UNLABELLED_LOCATION_RE = re.compile(r"^(?!Location:)(?=[A-Z][A-Za-z .'-]*, [A-Za-z .'-]+ County\b)", re.M)
_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
# Every item's first cell is its facility type. Status and date cells from
# the previous row run straight into it in the extracted text ("...Approved
# 10/16/25 Nursing Home Tupelo Community Care Center"), so an item's head is
# taken from the LAST type label before its "Transaction:".
_FACILITY_TYPES = {
    "Nursing Home": "Nursing Home", "NH": "Nursing Home", "Medical Center": "Medical Center",
    "Hospital": "Hospital", "Retirement Home": "Retirement Home", "Home Health": "Home Health",
    "Hospice": "Hospice", "ESRD Facility": "ESRD Facility", "ASC": "Surgery Center",
    "Surgery Center": "Surgery Center", "Surgical Center": "Surgery Center",
    "Personal Care Home": "Personal Care Home", "Psychiatric Residential Treatment Facility": "PRTF",
}
_TYPE_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _FACILITY_TYPES), key=len, reverse=True)) + r")\s+")
_STATUS_CELL_RE = re.compile(
    r"\d{1,2}/\d{1,2}/\d{2,4}|\((?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun|Holi)day\)|\bN/A\b|"
    r"\b(?:Approved|Rejected|Returned|Withdrawn|Received|(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day)\b|"
    r"after completion", re.I)
_NURSING_WORDS_RE = re.compile(r"long[- ]term care|nursing|skilled", re.I)
_BOILERPLATE_RE = re.compile(r"Legend Columns in Red.*?Page \d+ of \d+|As of Week Ending \S+|"
                             r"Items in Bold and Italics = New information added since last Weekly Report\.?", re.I)


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def _reports_on(page_url: str) -> list[tuple[date, str]]:
    page = _get(page_url).text
    return [(datetime.strptime(d, "%B %d, %Y").date(), urljoin(page_url, href))
            for href, d in _REPORT_LINK_RE.findall(page)]


def _archive_page_for(year: int) -> str | None:
    """The archive index lists weekly-report pages first, labelled by year."""
    page = _get(CON_MS_ARCHIVE_URL).text
    for href, label in re.findall(r'href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
        if re.sub(r"<[^>]+>|\s+", "", label) == str(year):
            return urljoin(CON_MS_ARCHIVE_URL, href)
    return None


def _parse_date(s: str) -> date | None:
    m = _DATE_RE.search(s or "")
    if not m:
        return None
    y = int(m.group(3))
    return date(y + 2000 if y < 100 else y, int(m.group(1)), int(m.group(2)))


def _parse_chow_items(text: str) -> list[dict]:
    start = _SECTION_START_RE.search(text)
    if not start:
        return []
    section = _UNLABELLED_LOCATION_RE.sub("Location: ", text[start.end():])
    end = re.search(r"SMALL COMMUNITY HOSPITAL|Statutory Exemption", section, re.I)
    section = section[:end.start()] if end else section
    header = _HEADER_END_RE.search(section)
    section = section[header.end():] if header else section
    section = re.sub(r"\s+", " ", _BOILERPLATE_RE.sub(" ", re.sub(r"\s+", " ", section)))

    items = []
    prev_end = 0
    for m in _ITEM_RE.finditer(section):
        between = section[prev_end:m.start()]
        prev_end = m.end()
        received = _parse_date(section[m.end():m.end() + 40])
        # The row starts at the first type label after the previous row's
        # last date/status cell; a later label is part of the name
        # ("Bolivar Medical Center Long Term Care")
        cells = list(_STATUS_CELL_RE.finditer(between))
        cut = cells[-1].end() if cells else 0
        types = list(_TYPE_RE.finditer(between, cut)) or list(_TYPE_RE.finditer(between))[-1:]
        if not types:
            continue  # can't find where this row starts; don't guess a name
        ftype = _FACILITY_TYPES[types[0].group(1)]
        head = between[types[0].end():].strip()
        licensee = None
        if "Licensee Name:" in head:
            head, licensee = [p.strip() for p in head.split("Licensee Name:", 1)]
        # "Academy Health Center, Inc. (currently being leased to Lamar Health and Rehabilitation Center)"
        leased = re.match(r"(.*?)\s*\((?:currently\s+)?(?:being\s+)?leased\s+to\s+(.*?)\)?$", head, re.I)
        if leased:
            head, licensee = leased.group(1).strip(" ,"), licensee or leased.group(2).strip()
        items.append({
            "type": ftype,
            "facility": head,
            "licensee": licensee,
            "transaction": m.group("tx").strip(),
            "location": re.sub(r",?\s*Mississippi$", "", m.group("loc").strip()).strip(" ,"),
            "received": received,
        })
    return items


def _is_nursing_home(item: dict) -> bool:
    return item["type"] == "Nursing Home" or bool(_NURSING_WORDS_RE.search(item["facility"]))


def record_url(item: dict) -> str:
    """Keyed on the name's first word + place + date: the state's own table
    has typos between weeks ("Chadwick Commiunity Care Center")."""
    first_word = re.sub(r"[^a-z0-9]", "", (item["facility"].split() or ["x"])[0].lower())
    place = re.sub(r"[^a-z0-9]+", "-", item["location"].lower()).strip("-")
    return f"{CON_MS_INDEX_URL}#chow-{first_word}-{place}-{item['received']:%Y%m%d}"


def fetch_con_ms_deals(is_known, backfill: bool) -> list[dict]:
    """
    Pre-extracted deal dicts for nursing home CHOW applications not yet stored.
    backfill=True reads every weekly report in the recency window (first run);
    otherwise only the latest RECENT_REPORTS.
    """
    today = date.today()
    try:
        reports = _reports_on(CON_MS_INDEX_URL)
    except Exception as e:
        logger.error(f"MS CON weekly report page fetch failed: {e}")
        health.source_failed("MS CON weekly reports", f"index fetch failed: {e}")
        return []
    if not reports:
        health.source_failed("MS CON weekly reports", "0 weekly reports listed (layout change?)")
        return []
    cutoff = today - timedelta(days=CON_MS_RECENCY_DAYS)
    if backfill:
        try:
            archive = _archive_page_for(today.year - 1)
            if archive:
                reports += _reports_on(archive)
        except Exception as e:
            logger.warning(f"MS CON {today.year - 1} archive fetch failed: {e}")
        reports = [r for r in reports if r[0] >= cutoff]
    reports = sorted(set(reports), reverse=True)
    if not backfill:
        reports = reports[:RECENT_REPORTS]
    logger.info(f"MS CON: reading {len(reports)} weekly reports ({'backfill' if backfill else 'recent'})")

    deals, seen = [], set()
    weeks_with_section = 0
    for week, url in reports:
        health.attempted("MS CON report parse")
        try:
            text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(_get(url, timeout=120).content)).pages)
            items = _parse_chow_items(text)
        except Exception as e:
            logger.warning(f"MS CON report {week} failed: {e}")
            health.failed("MS CON report parse", f"{url}: {e}")
            continue
        if not _SECTION_START_RE.search(text):
            # Normal in a week with no active CHOWs (e.g. week ending 11/28/25)
            logger.info(f"MS CON report {week}: no CHOW section")
            continue
        weeks_with_section += 1

        for item in items:
            if not item["received"] or item["received"] < cutoff or not _is_nursing_home(item):
                continue
            url_key = record_url(item)
            if url_key in seen or is_known(url_key):
                continue
            seen.add(url_key)

            summary = (
                f"Mississippi CHOW application for {item['facility']} "
                f"({item['type'] or 'facility'}; {item['location']}), transaction: "
                f"{item['transaction']}, received {item['received']:%m/%d/%Y}"
                + (f", licensee {item['licensee']}" if item["licensee"] else "")
                + ". MSDH does not publish the buyer."
            )
            deals.append({
                "url": url_key,
                "title": f"[MS CON] CHOW ({item['transaction']}): {item['facility']}",
                "published_at": datetime.combine(item["received"], datetime.min.time()).replace(tzinfo=timezone.utc),
                "raw_text": f"{summary}\n\nSource: {url} (week ending {week:%m/%d/%Y})",
                "source_type": "con",
                "pre_extracted": True,
                "extraction_model": "con_direct",
                "_con_id": url_key.rsplit("#", 1)[1],
                "acquiring_entity": None,
                "seller_entity": None,
                "operator_names": [item["licensee"]] if item["licensee"] else [],
                "facility_names": [item["facility"]],
                "states": ["MS"],
                "facility_count": 1,
                "deal_value_m": None,
                "acquisition_date": item["received"].isoformat(),
                "financing_amount_m": None,
                "lender": None,
                "rationale": summary,
            })

    if reports and not weeks_with_section:
        health.source_failed("MS CON weekly reports", "no CHOW section in any report read (layout change?)")
    logger.info(f"MS CON: {len(deals)} new nursing home CHOW applications")
    return deals
