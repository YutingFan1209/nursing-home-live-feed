"""
Montana DPHHS Certificate of Need monthly reports -- nursing home CHOWs.

Since SB 88 (7/1/2025, and in practice since spring 2025) a long-term care
change of ownership must be reported to the CON program, which lists it as
a non-reviewable "CHOW" row in its monthly report: facility, town, date
received, decision date. One PDF per year holds every month's report. The
buyer is not published, so these are facility-level notices like MS's.
See docs/con-feasibility.md (Montana).

Rows repeat month after month until they drop off, so each home is keyed on
name + date received. The extracted text runs rows together, so a row is
"<facility> <town> CHOW N/A <date>", the facility being whatever follows the
previous row's last status cell; the town is split off with the CMS list of
Montana cities. The state's own table has typos ("CHNOW").
"""

import io
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests
from pypdf import PdfReader

from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_MT_SOURCE_NAME = "Montana DPHHS Certificate of Need Monthly Reports"
CON_MT_INDEX_URL = "https://dphhs.mt.gov/oig/licensure/healthcarefacilitylicensure/certificateofneed/"
FIRST_YEAR = 2025  # first CHOW rows: Discovery Care Centre, April 2025

_REPORT_LINK_RE = re.compile(r'href="([^"]*CertificateofNeed/(\d{4})CONreports\.pdf)"', re.I)
_ROW_RE = re.compile(r"(?P<head>[A-Z][^/]*?)\s+CH\w{0,2}OW\s+N/A\s+(?P<date>\d{1,2}/\d{1,2}/\d{2,4})")
# Cells that end the previous row, so the next facility starts after them
_CELL_END_RE = re.compile(r".*(?:\bN/A|\bNR|\bY|\bN|\d{1,2}/\d{1,2}/\d{2,4}|Page \d+ of \d+|\bREQ)\s+", re.S)
# Leftover cells: the LOI month column ("5/6/26 May Northern Pines") and a
# split "N/ A"
_LEADING_CELLS_RE = re.compile(r"^(?:(?:A|N/A|NR|N|Y|January|February|March|April|May|June|July|August|September"
                               r"|October|November|December|Sept)\s+)+")


def _date(s: str):
    mo, d, y = (int(x) for x in s.split("/"))
    return datetime(y + 2000 if y < 100 else y, mo, d, tzinfo=timezone.utc)


def parse_chows(report_text: str, cities: set[str]) -> list[dict]:
    text = " ".join(report_text.split())
    rows = []
    for m in _ROW_RE.finditer(text):
        head = _LEADING_CELLS_RE.sub("", _CELL_END_RE.sub("", m.group("head")).strip())
        words = head.split(" ")
        # Longest trailing run of words that is a Montana city ("Cut Bank")
        for n in (3, 2, 1):
            if len(words) > n and " ".join(words[-n:]).upper() in cities:
                facility, town = " ".join(words[:-n]), " ".join(words[-n:])
                break
        else:
            facility, town = head, None
        rows.append({"facility": facility, "town": town, "received": _date(m.group("date"))})
    return rows


_NAME_NOISE_RE = re.compile(r"\b(LLC|INC|THE|OF|AND|AT|CENTER|CENTRE|HEALTH|HEALTHCARE|CARE|NURSING|REHABILITATION|REHAB)\b")


def _name_core(name: str) -> str:
    return " ".join(_NAME_NOISE_RE.sub(" ", re.sub(r"[^A-Z0-9 ]", " ", name.upper())).split())


def match_ccn(row: dict, cms_homes: list[tuple[str, str, str]]) -> str | None:
    """CCN of the CMS home in the row's town whose name agrees: "Copper Ridge
    Nursing and Rehabilitation" is CMS's "COPPER RIDGE HEALTH AND
    REHABILITATION CENTER"; Deer Lodge has two homes, so the town alone
    is never enough."""
    core = _name_core(row["facility"])
    hits = [ccn for name, city, ccn in cms_homes
            if (not row["town"] or (city or "").upper() == row["town"].upper())
            and core and (core == _name_core(name) or core.split()[:2] == _name_core(name).split()[:2])]
    return hits[0] if len(hits) == 1 else None


def record_url(row: dict) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", row["facility"].lower()).strip("-")
    return f"{CON_MT_INDEX_URL}#chow-{key}-{row['received']:%Y%m%d}"


def fetch_con_mt_deals(is_known, cms_homes: list[tuple[str, str, str]]) -> list[dict]:
    """cms_homes: (provider_name, provider_city, ccn) of every MT home in CMS."""
    cities = {(city or "").upper() for _, city, _ in cms_homes if city}
    try:
        page = cffi_requests.get(CON_MT_INDEX_URL, impersonate="chrome", timeout=60)
        page.raise_for_status()
    except Exception as e:
        logger.error(f"MT CON page fetch failed: {e}")
        health.source_failed("MT CON reports", f"{CON_MT_INDEX_URL}: {e}")
        return []
    reports = sorted({(int(y), urljoin(CON_MT_INDEX_URL, href)) for href, y in _REPORT_LINK_RE.findall(page.text)
                      if int(y) >= FIRST_YEAR}, reverse=True)
    if not reports:
        health.source_failed("MT CON reports", "no yearly CON report links on the page (layout change?)")
        return []

    deals, seen = [], set()
    for year, url in reports:
        try:
            resp = cffi_requests.get(url, impersonate="chrome", timeout=120)
            resp.raise_for_status()
            text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(resp.content)).pages)
        except Exception as e:
            logger.error(f"MT CON report {year} failed: {e}")
            health.source_failed("MT CON reports", f"{url}: {e}")
            continue
        for row in parse_chows(text, cities):
            key = record_url(row)
            if key in seen or is_known(key):
                continue
            seen.add(key)
            ccn = match_ccn(row, cms_homes)
            summary = (
                f"Montana DPHHS Certificate of Need report: change of ownership (CHOW) notice for "
                f"{row['facility']}" + (f", {row['town']}" if row["town"] else "")
                + f", received {row['received']:%m/%d/%Y} (non-reviewable). DPHHS does not publish the buyer."
            )
            deals.append({
                "url": key,
                "title": f"[MT CON] CHOW: {row['facility']}" + (f" ({row['town']})" if row["town"] else ""),
                "published_at": row["received"],
                "raw_text": f"{summary}\n\nSource: {url}",
                "source_type": "con",
                "pre_extracted": True,
                "extraction_model": "con_direct",
                "_con_id": key.rsplit("#", 1)[1],
                "acquiring_entity": None,
                "seller_entity": None,
                "operator_names": [],
                "facility_names": [row["facility"]],
                # Linked by name + town to one CMS home, not by owner names
                **({"ccns": [ccn]} if ccn else {}),
                "states": ["MT"],
                "facility_count": 1,
                "deal_value_m": None,
                "acquisition_date": row["received"].date().isoformat(),
                "financing_amount_m": None,
                "lender": None,
                "rationale": summary,
            })
    logger.info(f"MT CON: {len(deals)} new nursing home CHOW notices")
    return deals
