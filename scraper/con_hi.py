"""
Hawaii SHPDA Certificate of Need applications -- nursing facility
acquisitions.

Buying a skilled nursing / intermediate care facility in Hawaii needs a CON
(administrative review), and SHPDA lists every application on one page with
its number, applicant and project ("Acquisition of Skilled Nursing
Facility/Intermediate Care Facility services at <address>"), plus a link to
the decision. Very low volume: 24-04A (2024) and a batch of four in 2022.
See docs/con-feasibility.md (Hawaii).

The applicant is the buyer. The page gives the facility's address, not its
name, and no seller or price, so these take the pre_extracted path (no
Claude) and the address goes in the summary rather than a guessed name.
"""

import html
import logging
import re
from datetime import date, datetime, timezone
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests

from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_HI_SOURCE_NAME = "Hawaii SHPDA Certificate of Need Applications"
CON_HI_INDEX_URL = "https://health.hawaii.gov/shpda/certificate-of-need-applications-and-decisions/"

FIRST_YEAR = 2024  # 24-04A is the latest nursing facility acquisition (2026-10)

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_NUMBER_RE = re.compile(r"^(\d{2})-\d{2}[A-Za-z]?$")
_NURSING_RE = re.compile(r"skilled nursing|\bSNF\b|nursing (home|facility|center)|intermediate care facility\b", re.I)
_OWNERSHIP_RE = re.compile(r"\bacquisition of\b.*\b(services|facility|beds?)\b|change (of|in) ownership|transfer of ownership", re.I)
# Adding, deleting or converting beds is not a sale
_BEDS_ONLY_RE = re.compile(r"^(addition|deletion|conversion|change of \d|establishment)", re.I)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_rows(page_html: str) -> list[dict]:
    rows = []
    for row in _ROW_RE.findall(page_html):
        cells = _CELL_RE.findall(row)
        if len(cells) < 2:
            continue
        number = _clean(cells[0])
        m = _NUMBER_RE.match(number)
        if not m:
            continue
        text = _clean(cells[1])
        applicant, _, project = text.partition(":")
        decision = re.search(r"href=[\"']([^\"']+)[\"']", cells[2]) if len(cells) > 2 else None
        rows.append({"number": number.upper(), "year": 2000 + int(m.group(1)),
                     "applicant": applicant.strip(), "project": project.strip() or text,
                     "decision_url": urljoin(CON_HI_INDEX_URL, decision.group(1)) if decision else None})
    return rows


def is_nursing_acquisition(row: dict) -> bool:
    p = row["project"]
    return bool(_NURSING_RE.search(p) and _OWNERSHIP_RE.search(p) and not _BEDS_ONLY_RE.match(p))


def fetch_con_hi_deals(is_known) -> list[dict]:
    try:
        resp = cffi_requests.get(CON_HI_INDEX_URL, impersonate="chrome", timeout=60)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"HI CON page fetch failed: {e}")
        health.source_failed("HI CON applications", f"{CON_HI_INDEX_URL}: {e}")
        return []
    rows = parse_rows(resp.text)
    deals = []
    for row in rows:
        url = f"{CON_HI_INDEX_URL}#app-{row['number']}"
        if row["year"] < FIRST_YEAR or not is_nursing_acquisition(row) or is_known(url):
            continue
        place = re.search(r"\b(?:at|located at)\s+(.*)$", row["project"])
        summary = (
            f"Hawaii SHPDA Certificate of Need application {row['number']}: {row['applicant']}, "
            f"{row['project'][0].lower() + row['project'][1:]}. SHPDA's list names the applicant (the buyer) "
            f"and the facility's address{', ' + place.group(1) if place else ''}; it gives no seller or price."
            + (" A decision has been issued." if row["decision_url"] else " No decision posted yet.")
        )
        deals.append({
            "url": url,
            "title": f"[HI CON] {row['number']} {row['applicant']}: {row['project'][:120]}",
            "published_at": datetime(row["year"], 1, 1, tzinfo=timezone.utc),
            "raw_text": summary + (f"\n\nDecision: {row['decision_url']}" if row["decision_url"] else ""),
            "source_type": "con",
            "pre_extracted": True,
            "extraction_model": "con_direct",
            "_con_id": f"HI-{row['number']}",
            "acquiring_entity": row["applicant"],
            "seller_entity": None,
            "operator_names": [],
            "facility_names": [],
            "states": ["HI"],
            "facility_count": 1,
            "deal_value_m": None,
            "acquisition_date": None,
            "financing_amount_m": None,
            "lender": None,
            "rationale": summary,
        })
    logger.info(f"HI CON: {len(rows)} applications listed, {len(deals)} new nursing facility acquisitions")
    return deals
