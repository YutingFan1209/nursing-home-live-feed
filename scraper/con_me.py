"""
Maine DHHS Certificate of Need reviews -- nursing facility cases.

22 M.R.S. §329(1) puts any transfer of ownership or acquisition of control
of a nursing facility under CON review, and DHHS lists every open case on
one page ("Current Healthcare Reviews", plus a page per prior year). A case
starts with a Letter of Intent naming the buyer, seller and facilities --
months before an application, a decision, or anything in CMS (the Eagle
Arc/Links LOI was 35 days ahead of the news). Low volume: a handful of
nursing facility cases a year. See docs/con-feasibility.md (Maine section).

Page layout: an <h2> "Nursing Facility Reviews" section, then per case an
<h3> title, an italic transaction type, and a list of dated documents that
grows as the case advances (LOI -> legal notice -> analysis -> decision).
A case is keyed on its FIRST document's URL, which stays put as documents
are appended, so each case is extracted once, from that first filing. Later
filings are listed in the header for context but not re-extracted.

Some filings are scanned images; pipeline.pdf_text transcribes those.
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

CON_ME_SOURCE_NAME = "Maine DHHS Certificate of Need Reviews"
CON_ME_INDEX_URL = "https://www.maine.gov/dhhs/dlc/healthcare-oversight/current_healthcare_reviews"
# Prior years live at older-reviews/<year>-health-care-review
CON_ME_YEAR_URL = "https://www.maine.gov/dhhs/dlc/healthcare-oversight/older-reviews/{year}-health-care-review"

CON_ME_RECENCY_DAYS = 365
MAX_PDF_PAGES = 8

_SECTION_RE = re.compile(r"<h2[^>]*>(?:\s|<[^>]+>)*Nursing Facility Reviews.*?</h2>(.*?)(?=<h2|$)", re.S | re.I)
_CASE_RE = re.compile(r"<h3[^>]*>(.*?)</h3>(.*?)(?=<h3|$)", re.S)
_DOC_RE = re.compile(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>\s*\(([^)]*)\)', re.S)
_DATE_RE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _parse_cases(page_html: str, page_url: str) -> list[dict]:
    section = _SECTION_RE.search(page_html)
    if not section:
        return []
    cases = []
    for title, body in _CASE_RE.findall(section.group(1)):
        kind = re.search(r"<em>(.*?)</em>", body, re.S)
        docs = []
        for href, label, note in _DOC_RE.findall(body):
            d = _DATE_RE.search(note)
            try:
                when = datetime.strptime(d.group(1), "%B %d, %Y").date() if d else None
            except ValueError:
                when = None
            docs.append({"url": urljoin(page_url, href), "label": _clean(label), "note": _clean(note), "date": when})
        if docs:
            cases.append({"title": _clean(title), "kind": _clean(kind.group(1)) if kind else "", "docs": docs})
    return cases


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def fetch_con_me_cases(is_known) -> list[dict]:
    """
    Return article dicts (raw_text filled, ready for Claude extraction) for
    nursing facility cases whose first document isn't stored yet.
    """
    pages = [CON_ME_INDEX_URL, CON_ME_YEAR_URL.format(year=date.today().year - 1)]
    cases = []
    for i, url in enumerate(pages):
        try:
            cases += _parse_cases(_get(url).text, url)
        except Exception as e:
            # The current page is the one that matters; last year's is backfill
            if i == 0:
                logger.error(f"ME CON page fetch failed: {e}")
                health.source_failed("ME CON reviews", f"{url}: {e}")
                return []
            logger.warning(f"ME CON prior-year page fetch failed: {e}")

    cutoff = date.today() - timedelta(days=CON_ME_RECENCY_DAYS)
    articles = []
    for case in cases:
        first = case["docs"][0]
        filed = first["date"] or min((d["date"] for d in case["docs"] if d["date"]), default=None)
        if not filed or filed < cutoff or is_known(first["url"]):
            continue

        health.attempted("ME CON filing download")
        try:
            text, _ = pdf_text(_get(first["url"], timeout=120).content, MAX_PDF_PAGES, case["title"])
        except Exception as e:
            logger.warning(f"ME CON {case['title']} download/parse failed: {e}")
            health.failed("ME CON filing download", f"{first['url']}: {e}")
            continue

        docket = "\n".join(f"- {d['label']} ({d['note']})" for d in case["docs"])
        articles.append({
            "url": first["url"],
            "title": f"[ME CON] {case['title']}: {case['kind']}",
            "published_at": datetime.combine(filed, datetime.min.time()).replace(tzinfo=timezone.utc),
            "raw_text": (
                f"Maine DHHS Certificate of Need review — nursing facility case "
                f"\"{case['title']}\" ({case['kind']}). First filing: {first['label']}, "
                f"{first['note']}. This is a regulatory filing for a pending transaction, "
                f"i.e. current news. Report it as ONE deal covering every Maine facility "
                f"named, with acquisition_date = the expected closing date if stated, "
                f"else null.\n\nFilings in this case so far:\n{docket}\n\n"
                f"Text of the {first['label']}:\n\n{text}"
            ),
            "source_type": "con",
        })

    logger.info(f"ME CON: {len(cases)} nursing facility cases listed, {len(articles)} new")
    return articles
