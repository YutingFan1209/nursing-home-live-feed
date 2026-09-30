"""
Maryland MHCC nursing home acquisition applications.

Health-General §19-120.2 and COMAR 10.24.01.21 require MHCC approval before
anyone acquires a Maryland nursing home, requested at least 60 days before
closing, with a 30-day public comment period. MHCC lists every case on one
page, active and completed, each with its application PDFs. MHCC posted the
18-home CommuniCare sale about 170 days before the tracker saw it in an SEC
filing (docs/con-feasibility.md, "Beyond NCSL's list").

Page layout: an <h2> per case ("Montcare Healthcare, LLC"), then accordion
panels ("Applications", "Staff Report and Recommendation", ...) holding the
document links; multi-facility cases name each facility, either as a bold
sub-heading (Montcare) or as the link text itself (CommuniCare's 18). The
page has no dates, but upload paths carry the month (/files/2026-09/...),
which stands in for the filing date. It can run late when documents are
re-uploaded: CommuniCare's were posted 2026-02-09 but re-uploaded (redacted)
in May.

Like Maine, a case is keyed on its first document and extracted once by
Claude from that application, with every facility named in the header.
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

CON_MD_SOURCE_NAME = "Maryland MHCC Nursing Home Acquisition Applications"
CON_MD_INDEX_URL = ("https://mhcc.maryland.gov/healthcare-communities/state-health-planning-and-certificate-need-con/"
                    "acquisition-or-change-ownership/nursing-home-acquisition-applications")
CON_MD_RECENCY_DAYS = 365
MAX_PDF_PAGES = 8

_CASE_RE = re.compile(r'blocksubsection-header">\s*<h2>(.*?)</h2>(.*?)(?=blocksubsection-header|<h2>Questions</h2>|$)', re.S)
_PANEL_RE = re.compile(r'accordion-button[^>]*>(.*?)</button>.*?accordion-body[^>]*>(.*?)</div>', re.S)
_LINK_RE = re.compile(r'<a [^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.S)
_UPLOAD_MONTH_RE = re.compile(r"/(\d{4})-(\d{2})/")
# Documents that aren't about a specific facility
_GENERIC_LINK_RE = re.compile(r"^(application|addendum|completeness|attachments?|ownership|mq\d|organizational|applicant|"
                              r"acquisition of operations|approval|staff|order|supplemental)", re.I)


# Link text that names a document or the buyer rather than a facility
# ("CommuniCare Response to Completeness Questions", "Silver Spring Opco, LLC")
_NOT_FACILITY_RE = re.compile(r"response|completeness|question|\b(?:llc|inc|l\.?p)\.?$", re.I)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def _parse_cases(page_html: str) -> list[dict]:
    cases = []
    for title, body in _CASE_RE.findall(page_html):
        title = _clean(title)
        if not title or title.lower() in ("active applications", "completed applications"):
            continue
        docs, facilities = [], []
        for panel, panel_body in _PANEL_RE.findall(body):
            panel = _clean(panel)
            for sub in re.findall(r"<strong>(.*?)</strong>", panel_body, re.S):
                facilities.append(_clean(sub))
            for href, label in _LINK_RE.findall(panel_body):
                label = _clean(label)
                docs.append({"url": urljoin(CON_MD_INDEX_URL, href), "label": label, "panel": panel})
                if (panel.lower().startswith("application") and not _GENERIC_LINK_RE.match(label)
                        and not _NOT_FACILITY_RE.search(label)):
                    facilities.append(label)
        if not docs:
            continue
        months = [date(int(y), int(m), 1) for d in docs for y, m in _UPLOAD_MONTH_RE.findall(d["url"])]
        cases.append({
            "title": title,
            "docs": docs,
            "facilities": list(dict.fromkeys(f for f in facilities if f)),
            "filed": min(months) if months else None,
        })
    return cases


def fetch_con_md_cases(is_known) -> list[dict]:
    """Article dicts (raw_text filled, for Claude) for cases whose first document isn't stored yet."""
    try:
        cases = _parse_cases(_get(CON_MD_INDEX_URL).text)
    except Exception as e:
        logger.error(f"MD CON page fetch failed: {e}")
        health.source_failed("MD CON acquisitions", f"page fetch failed: {e}")
        return []
    if not cases:
        health.source_failed("MD CON acquisitions", "page parsed to 0 cases (layout change?)")
        return []

    cutoff = date.today() - timedelta(days=CON_MD_RECENCY_DAYS)
    articles = []
    for case in cases:
        first = case["docs"][0]
        if not case["filed"] or case["filed"] < cutoff.replace(day=1) or is_known(first["url"]):
            continue
        health.attempted("MD CON application download")
        try:
            text, _ = pdf_text(_get(first["url"], timeout=120).content, MAX_PDF_PAGES, case["title"])
        except Exception as e:
            logger.warning(f"MD CON {case['title']} download/parse failed: {e}")
            health.failed("MD CON application download", f"{first['url']}: {e}")
            continue

        facilities = case["facilities"] or [case["title"]]
        docket = "\n".join(f"- {d['panel']}: {d['label']}" for d in case["docs"])
        articles.append({
            "url": first["url"],
            "title": f"[MD CON] Nursing home acquisition application: {case['title']}",
            "published_at": datetime.combine(case["filed"], datetime.min.time()).replace(tzinfo=timezone.utc),
            "raw_text": (
                f"Maryland Health Care Commission (MHCC) nursing home acquisition application — "
                f"case \"{case['title']}\", posted {case['filed']:%B %Y}. MHCC approval is required "
                f"before closing, so this is a pending or recent transaction, i.e. current news. "
                f"Report it as ONE deal covering all {len(facilities)} Maryland nursing home(s) in "
                f"this case: {'; '.join(facilities)}. acquiring_entity = the applicant/purchaser "
                f"named in the application; acquisition_date = the proposed closing date if "
                f"stated, else null. "
                + (f"The text below is only the first facility's application, so name the parties "
                   f"at the portfolio level: put the parent companies of the buyer and the seller "
                   f"(including any named in the case title, \"{case['title']}\", and any REIT or "
                   f"landlord selling the real estate) in operator_names, and use the seller's "
                   f"parent as seller_entity when it is named. " if len(facilities) > 1 else "")
                + f"\n\nDocuments in this case:\n{docket}\n\n"
                f"Text of \"{first['label']}\":\n\n{text}"
            ),
            "source_type": "con",
        })

    logger.info(f"MD CON: {len(cases)} cases listed, {len(articles)} new")
    return articles
