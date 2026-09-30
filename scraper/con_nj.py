"""
New Jersey DOH long-term care transfer-of-ownership filings.

N.J.S.A. 26:2H-7.25 et seq. require the prospective new owner of a nursing
home to apply to DOH before the transfer, with a 30-day public comment
period, and the owner of a nursing home's real estate to give notice of a
real estate transfer. DOH posts both on two pages:

  - Long-Term Care Facility Transfer of Ownership: accepted date, posting
    date, application number, facility / address / license, comment period,
    and usually a summary PDF naming the proposed owner and the current one
    (sometimes the full application, which may be a scan). One Claude
    extraction per application, from that PDF.
  - Real Estate Transfer of Ownership: notification date, "<seller> to
    <buyer>", facility. The parties are in the table, so these are parsed
    without Claude.

Only recent applications are listed on the operator page, so its history
builds up as the pipeline keeps running. See docs/con-feasibility.md
("Beyond NCSL's list").
"""

import html
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests

from pipeline.pdf_text import pdf_text
from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_NJ_SOURCE_NAME = "New Jersey DOH Long-Term Care Transfer of Ownership"
CON_NJ_OPERATOR_URL = "https://www.nj.gov/health/healthfacilities/certificate-need/ltc-transfer-ownership"
CON_NJ_REAL_ESTATE_URL = "https://www.nj.gov/health/healthfacilities/certificate-need/real-estate-transfer-ownership"
CON_NJ_RECENCY_DAYS = 365
MAX_PDF_PAGES = 6

_DATE_RE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")
_PDF_RE = re.compile(r'href="([^"]+\.pdf)"')


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _date(text: str) -> date | None:
    m = _DATE_RE.search(text or "")
    try:
        return datetime.strptime(m.group(1), "%B %d, %Y").date() if m else None
    except ValueError:
        return None


def _table_rows(page_html: str, page_url: str) -> list[tuple[list[str], list[str]]]:
    """(cell texts, absolute PDF links) for each data row."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S):
        cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if cells:
            rows.append((cells, [urljoin(page_url, h) for h in _PDF_RE.findall(tr)]))
    return rows


def _facility_name(info: str) -> str:
    """'Name: Silver Healthcare Center Address: ...' or 'Green Hill 103 Pleasant Valley Way, ...'"""
    m = re.search(r"Name:\s*(.*?)\s*(?:Address:|License|$)", info)
    if m:
        return m.group(1).strip()
    return re.split(r"\s+\d", info, maxsplit=1)[0].strip(" ,")


def _operator_articles(is_known, cutoff: date) -> list[dict]:
    rows = _table_rows(_get(CON_NJ_OPERATOR_URL).text, CON_NJ_OPERATOR_URL)
    articles = []
    for cells, pdfs in rows:
        if len(cells) < 4:
            continue
        accepted, app_no, info = _date(cells[0]), cells[2].replace("App #", "").strip(), cells[3]
        facility = _facility_name(info)
        if not accepted or accepted < cutoff or not facility:
            continue
        url = pdfs[0] if pdfs else f"{CON_NJ_OPERATOR_URL}#{app_no}"
        if is_known(url):
            continue
        text = ""
        if pdfs:
            health.attempted("NJ CON document download")
            try:
                text, _ = pdf_text(_get(pdfs[0], timeout=120).content, MAX_PDF_PAGES, facility)
            except Exception as e:
                logger.warning(f"NJ CON {app_no} document failed: {e}")
                health.failed("NJ CON document download", f"{pdfs[0]}: {e}")
        articles.append({
            "url": url,
            "title": f"[NJ CON] Transfer of ownership application {app_no}: {facility}",
            "published_at": datetime.combine(accepted, datetime.min.time()).replace(tzinfo=timezone.utc),
            "raw_text": (
                f"New Jersey Department of Health — long-term care facility transfer of ownership "
                f"application {app_no}, accepted {accepted:%B %d, %Y}; comment period {cells[4] if len(cells) > 4 else 'n/a'}. "
                f"Facility: {info}. DOH approval is required before the transfer, so this is a "
                f"pending transaction, i.e. current news. It covers exactly ONE facility "
                f"({facility}, NJ): report exactly one deal, with acquiring_entity = the proposed "
                f"owner of operations and seller_entity = the current owner/licensee.\n\n{text}"
            ),
            "source_type": "con",
        })
    return articles


def _real_estate_deals(is_known, cutoff: date) -> list[dict]:
    rows = _table_rows(_get(CON_NJ_REAL_ESTATE_URL).text, CON_NJ_REAL_ESTATE_URL)
    deals = []
    for cells, pdfs in rows:
        if len(cells) < 3:
            continue
        notified, parties, info = _date(cells[0]), cells[1], cells[2]
        facility = _facility_name(info)
        if not notified or notified < cutoff or not facility:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", facility.lower()).strip("-")[:50]
        url = f"{CON_NJ_REAL_ESTATE_URL}#{slug}-{notified:%Y%m%d}"
        if is_known(url):
            continue
        # "Troy NH, LLC and Troy 1997 LLC to Troy Hill Propco LLC"; some rows name only the buyer
        seller, _, buyer = re.sub(r"^From\s+", "", parties).rpartition(" to ")
        buyer = buyer.strip() or parties
        summary = (
            f"New Jersey nursing home real estate transfer: {parties}. Facility: {info}. "
            f"Notice received {notified:%m/%d/%Y}."
        )
        deals.append({
            "url": url,
            "title": f"[NJ CON] Real estate transfer: {facility}",
            "published_at": datetime.combine(notified, datetime.min.time()).replace(tzinfo=timezone.utc),
            "raw_text": f"{summary}\n\nSource: {pdfs[0] if pdfs else CON_NJ_REAL_ESTATE_URL}",
            "source_type": "con",
            "pre_extracted": True,
            "extraction_model": "con_direct",
            "_con_id": url.rsplit("#", 1)[1],
            "acquiring_entity": buyer,
            "seller_entity": seller.strip() or None,
            "operator_names": [],
            "facility_names": [facility],
            "states": ["NJ"],
            "facility_count": 1,
            "deal_value_m": None,
            "acquisition_date": notified.isoformat(),
            "financing_amount_m": None,
            "lender": None,
            "rationale": summary,
        })
    return deals


def fetch_con_nj(is_known) -> list[dict]:
    """Operator-transfer articles (for Claude) plus pre-extracted real estate transfer deals."""
    cutoff = date.today() - timedelta(days=CON_NJ_RECENCY_DAYS)
    out = []
    for label, fn in (("operator transfers", _operator_articles), ("real estate transfers", _real_estate_deals)):
        try:
            found = fn(is_known, cutoff)
            logger.info(f"NJ CON {label}: {len(found)} new")
            out += found
        except Exception as e:
            logger.error(f"NJ CON {label} fetch failed: {e}")
            health.source_failed(f"NJ CON {label}", e)
    return out
