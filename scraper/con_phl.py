"""
Philadelphia notices of ownership changes of long-term care facilities.

Philadelphia Code §6-409 makes anyone acquiring a nursing home (or
hospital) in the city notify the Department of Public Health before the
transfer, and the city posts each notice as a PDF on one page, with
exhibits (organization chart, the buyer's other facilities). These are
city-only filings: on 2026-10-05 none of the recent ones (Chapel Manor,
2026-09-29; five homes, 2025-12-17) were in the tracker. A PA add-on to
scraper/con_pa.py (docs/con-feasibility.md, build order #3).

Page layout: a list of PDF links titled with the facility name, newest
first. The upload timestamp is in each link (/media/20260929151037/...),
which dates the notice. Exhibits ("..., Exhibit A") and updates belong to
the notice before them. Hospital notices share the page and are skipped.
One Claude extraction per notice, keyed on its main PDF URL; scanned
PDFs are transcribed by pipeline.pdf_text. No robots.txt on phila.gov.
"""

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from pipeline.pdf_text import pdf_text
from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_PHL_SOURCE_NAME = "Philadelphia Notices of Long-Term Care Ownership Changes"
CON_PHL_INDEX_URL = ("https://www.phila.gov/documents/"
                     "notices-of-ownership-changes-of-long-term-care-facilities-and-hospitals/")

MAX_PDF_PAGES = 8
_MEDIA_DATE = re.compile(r"/media/(\d{8})\d*/")
_EXHIBIT = re.compile(r"\bexhibit\b|\bupdate\b", re.I)
_HOSPITAL = re.compile(r"\bhospitals?\b|healthcare network", re.I)


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def _parse_notices(page_html: str) -> list[dict]:
    """[{title, url, date, exhibits: [title]}], one per main notice PDF."""
    soup = BeautifulSoup(page_html, "html.parser")
    notices, seen = [], set()
    for a in (soup.find("main") or soup).find_all("a", href=True):
        href = urljoin(CON_PHL_INDEX_URL, a["href"])
        title = re.sub(r"\s*PDF$", "", a.get_text(" ", strip=True))
        # Each PDF is linked twice: titled, and as an untitled icon
        if "/media/" not in href or not href.lower().endswith(".pdf") or not title or href in seen:
            continue
        seen.add(href)
        m = _MEDIA_DATE.search(href)
        when = datetime.strptime(m.group(1), "%Y%m%d").date() if m else None
        notices.append({"title": title, "url": href, "date": when, "exhibits": []})
    # Exhibits and updates name their notice ("Chapel Manor, Exhibit A",
    # "Tulip Special Care, LLC (5/15/24 update)"); updates sit ABOVE it
    mains = [n for n in notices if not _EXHIBIT.search(n["title"])]
    for x in (n for n in notices if _EXHIBIT.search(n["title"])):
        owner = next((m for m in mains if x["title"].lower().startswith(m["title"].lower())), None)
        if owner:
            owner["exhibits"].append(x["title"])
    return mains


def fetch_con_phl_notices(is_known) -> list[dict]:
    """Article dicts (raw_text filled, for Claude extraction) for nursing
    home notices whose main PDF isn't stored yet."""
    try:
        notices = _parse_notices(_get(CON_PHL_INDEX_URL).text)
    except Exception as e:
        logger.error(f"Philadelphia notices page fetch failed: {e}")
        health.source_failed("PHL ownership notices", f"page fetch failed: {e}")
        return []
    if not notices:
        health.note("PHL ownership notices: no PDF links on the page (layout change?)")

    articles = []
    for n in notices:
        if _HOSPITAL.search(n["title"]) or is_known(n["url"]):
            continue
        health.attempted("PHL notice download")
        try:
            text, _ = pdf_text(_get(n["url"], timeout=120).content, MAX_PDF_PAGES, n["title"])
        except Exception as e:
            logger.warning(f"PHL notice {n['title']} download/parse failed: {e}")
            health.failed("PHL notice download", f"{n['url']}: {e}")
            continue
        posted = n["date"] or datetime.now(timezone.utc).date()
        exhibits = "".join(f"\n- {x}" for x in n["exhibits"])
        articles.append({
            "url": n["url"],
            "title": f"[Philadelphia notice] {n['title']}",
            "published_at": datetime.combine(posted, datetime.min.time()).replace(tzinfo=timezone.utc),
            "raw_text": (
                f"City of Philadelphia notice of a change of ownership of a long-term care "
                f"facility (Philadelphia Code 6-409), filed with the Department of Public "
                f"Health before the transfer and posted {posted}. This is a regulatory filing "
                f"for a pending transaction, i.e. current news. Report it as ONE deal covering "
                f"every facility named, with acquisition_date = the expected closing date if "
                f"stated, else null. Facility: {n['title']}."
                + (f"\nExhibits filed with it:{exhibits}" if exhibits else "")
                + f"\n\nText of the notice:\n\n{text}"
            ),
            "source_type": "con",
        })

    logger.info(f"PHL notices: {len(notices)} listed, {len(articles)} new")
    return articles
