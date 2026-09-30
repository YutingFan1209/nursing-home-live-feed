"""
Alabama SHPDA Notices of Change of Ownership (state CON-program filings).

Ala. Admin. Code r. 410-1-7-.04 requires a notice to SHPDA at least 20 days
before a change of ownership/control closes, and SHPDA posts every notice as
a PDF on one public index page. The PDF is the attorney's cover letter plus
the filing form, naming buyer, seller, APA date and expected closing -- so
this is typically weeks AHEAD of the closing, and months ahead of CMS CHOW.
See docs/con-feasibility.md (Alabama section) for the research behind this.

The index lists every facility type SHPDA regulates (hospice, home health,
assisted living...). The SHPDA facility ID quoted in each notice encodes the
type in its letter: "017-N0003" is a nursing home, "117-S3724" a specialty
care assisted living facility, "081-H7063" a home health agency. Only N
notices become extraction candidates; the rest are returned separately so
the caller can record them as seen without spending a Claude call on them.

Free-form letters vary too much for regex field extraction, so nursing home
notices go through the regular Claude extractor with the PDF text as
raw_text (a short header naming the filing is prepended for context). The
header pins the buyer to the per-facility proposed licensee: batch filings
(e.g. CO2026-062..068, seven Genesis facilities sold to one parent) otherwise
all extract the same parent/state/date, and the deals table's semantic-dedup
index silently drops all but the first as duplicates.

The new owner isn't in CMS yet when a notice is filed, so these deals stay
"detected" with no CMS match until the regular re-check sees CMS catch up.
"""

import io
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

from curl_cffi import requests as cffi_requests
from pypdf import PdfReader

from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_AL_SOURCE_NAME = "Alabama SHPDA Change of Ownership Notices"
CON_AL_INDEX_URL = "http://shpda.alabama.gov/Announcements/certificateofneed/chow/changeownershipnotice.aspx"

# Notices are filed >=20 days pre-close, so anything posted more than a year
# ago has long since closed and would be stale as a "live feed" item.
CON_AL_RECENCY_DAYS = 365

# Only the first few pages carry the cover letter and filing form; the rest
# is org charts and exhibits, which add nothing but extraction tokens.
MAX_PDF_PAGES = 6
# Below this, the PDF is a scan (page stamps like "CO2026-069" only)
MIN_TEXT_CHARS = 1500

_ROW_RE = re.compile(
    r'<a href="([^"]+\.pdf)"[^>]*>\s*(CO\d{4}-\d+)\s*</a>\s*</td>\s*'
    r'<td[^>]*>(.*?)</td>\s*<td[^>]*>\s*(\d{1,2}/\d{1,2}/\d{4})\s*</td>',
    re.S | re.I,
)
# SHPDA facility ID: 3-digit county code, type letter, 4 digits. Letters and
# digits sometimes get split by PDF text extraction ("017-N 0003").
_FACILITY_ID_RE = re.compile(r"\b(\d{3})\s*-\s*([A-Z])\s*(\d{4})\b")
_NURSING_HOME_TYPE = "N"
# Fallback when the ID can't be found (scanned pages, ID left off the form)
_NURSING_HOME_WORDS = re.compile(r"skilled nursing|nursing home|nursing facility|\bSNF\b", re.I)


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def _parse_index(html: str) -> list[dict]:
    notices = []
    for href, co_number, facility, date_str in _ROW_RE.findall(html):
        facility = re.sub(r"<[^>]+>|&nbsp;", " ", facility)
        facility = re.sub(r"\s+", " ", facility).strip()
        try:
            filed = datetime.strptime(date_str, "%m/%d/%Y").date()
        except ValueError:
            continue
        notices.append({
            "co_number": co_number,
            "facility": facility,
            "filed": filed,
            "url": urljoin(CON_AL_INDEX_URL, href),
        })
    return notices


def _pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages = reader.pages[:MAX_PDF_PAGES]
    return "\n".join((p.extract_text() or "") for p in pages)


def _facility_type(text: str, pdf_url: str) -> tuple[str | None, str | None]:
    """Return (facility_id, type_letter). The PDF filename often carries the
    ID too, which covers notices whose text layer is missing."""
    for haystack in (text, pdf_url.replace("%20", " ")):
        m = _FACILITY_ID_RE.search(haystack)
        if m:
            return f"{m.group(1)}-{m.group(2)}{m.group(3)}", m.group(2)
    return None, None


def fetch_con_al_notices(is_known) -> tuple[list[dict], list[dict]]:
    """
    Return (nursing_home_notices, skipped_notices) for notices not yet stored.

    is_known(url) -> bool says whether an article for that PDF already exists,
    so each notice's PDF is downloaded at most once over the pipeline's life.
    Nursing home notices are article dicts ready for Claude extraction (with
    raw_text filled in); skipped ones carry a "skip_reason" and should just be
    stored as seen.
    """
    try:
        notices = _parse_index(_get(CON_AL_INDEX_URL).text)
    except Exception as e:
        logger.error(f"AL CON index fetch failed: {e}")
        health.source_failed("AL CON notices", f"index fetch failed: {e}")
        return [], []
    if not notices:
        # The page loaded but nothing parsed -- layout change, not "no news"
        health.source_failed("AL CON notices", "index parsed to 0 notices (layout change?)")
        return [], []

    cutoff = date.today() - timedelta(days=CON_AL_RECENCY_DAYS)
    candidates = [n for n in notices if n["filed"] >= cutoff and not is_known(n["url"])]
    logger.info(f"AL CON: {len(notices)} notices listed, {len(candidates)} new within {CON_AL_RECENCY_DAYS} days")

    nursing_homes, skipped = [], []
    for n in candidates:
        health.attempted("AL CON notice download")
        try:
            text = _pdf_text(_get(n["url"], timeout=120).content)
        except Exception as e:
            logger.warning(f"AL CON {n['co_number']} download/parse failed: {e}")
            health.failed("AL CON notice download", f"{n['co_number']}: {e}")
            continue

        facility_id, type_letter = _facility_type(text, n["url"])
        article = {
            "url": n["url"],
            "title": f"[AL CON] {n['co_number']} Notice of Change of Ownership: {n['facility']}",
            "published_at": datetime.combine(n["filed"], datetime.min.time()).replace(tzinfo=timezone.utc),
            "raw_text": (
                f"Alabama State Health Planning and Development Agency (SHPDA) — "
                f"Notice of Proposed Change of Ownership {n['co_number']}, "
                f"posted {n['filed'].isoformat()}. Facility: {n['facility']} "
                f"(SHPDA ID: {facility_id or 'unknown'}), Alabama. This is a regulatory "
                f"filing for a pending or recent transaction, i.e. current news. "
                f"It covers exactly ONE facility ({n['facility']}): report exactly one deal, "
                f"with facility_count 1, acquiring_entity = the proposed licensee/new owner "
                f"entity named for this facility (not its parent), seller_entity = the current "
                f"licensee/owner, parent companies of either side in operator_names, and "
                f"acquisition_date = the expected closing or effective date.\n\n{text}"
            ),
            "source_type": "con",
        }

        is_nursing_home = (
            type_letter == _NURSING_HOME_TYPE
            if type_letter else bool(_NURSING_HOME_WORDS.search(text + " " + n["facility"]))
        )
        if is_nursing_home:
            nursing_homes.append(article)
        elif not type_letter and len(text.strip()) < MIN_TEXT_CHARS:
            # Scanned notice (e.g. CO2026-069, an Arabella nursing home): no
            # text layer means neither the facility type nor the parties can
            # be read without OCR. Surfaced rather than silently dropped.
            article["skip_reason"] = "AL CON: scanned PDF, no text layer (needs OCR)"
            health.note(f"AL CON {n['co_number']} ({n['facility']}) is a scanned PDF, not extracted: {n['url']}")
            skipped.append(article)
        else:
            article["skip_reason"] = f"AL CON: not a nursing home (SHPDA ID {facility_id or 'not found'})"
            skipped.append(article)

    logger.info(f"AL CON: {len(nursing_homes)} nursing home notices, {len(skipped)} other facility types")
    return nursing_homes, skipped
