"""
Illinois Health Facilities and Services Review Board (HFSRB) -- nursing home
change-of-ownership projects.

Most Illinois nursing home sales need no CON permit (an exemption, 20 ILCS
3960/8.5, and the 2026 exemption list had no nursing homes), but a sale of
a county-owned home does: 25-031 Oak Hill (Monroe County to Accolade) and
25-041 Stephenson County Nursing Center (to Freeport Nursing LLC), neither
of which the tracker had (docs/con-feasibility.md, Illinois). About two a
year, filed months before the Board votes.

The Board lists every project on one announcements page, each with its own
page of project documents. A project is read when its title looks like a
long-term care facility; it counts as a sale when its application is named
as a change of ownership or (these are scans, transcribed by Claude) lists
pre- and post-transaction owners. Modernizations, new builds and
discontinuations are stored as skipped so they aren't transcribed again.
Each project is keyed on its announcement page URL and extracted once.
"""

import html
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, unquote

from curl_cffi import requests as cffi_requests

from pipeline.pdf_text import pdf_text
from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_IL_SOURCE_NAME = "Illinois HFSRB Change of Ownership Projects"
CON_IL_INDEX_URL = "https://hfsrb.illinois.gov/announcements.html"

MAX_PDF_PAGES = 8
YEARS_BACK = 1  # project numbers from last year on ("25-031" in 2026)

_PROJECT_RE = re.compile(r'href="(/announcements/((?:\d{2}-\d{3})|(?:e-\d{3}-\d{2}))[^"]*\.html)"[^>]*>(.*?)</a>', re.I | re.S)
# Title words of a nursing facility (not hospitals, surgery or dialysis centers)
_LTC_TITLE_RE = re.compile(
    r"nursing|rehab(ilitation)? (and|&) (nursing|health|care)|care center|senior (living|community)|"
    r"manor|healthcare center|health care center|living center|convalescent|\bestates\b|skilled|"
    r"long[- ]term care|county home", re.I)
_NOT_LTC_RE = re.compile(r"hospital|surgery|surgical|dialysis|kidney|medical (office|center|group)|cancer|clinic|"
                         r"ambulatory|imaging|cardiac|eye ", re.I)
_CHOW_DOC_RE = re.compile(r"change[ _%20-]*of[ _%20-]*ownership|\bchow\b", re.I)
# The application form itself says "change of ownership" nowhere on its first
# pages, but a sale lists the site owner and licensee "(Pre-transaction)" and
# "(Post-transaction)": Stephenson County -> Freeport Realty LLC (25-041).
# DuPage Care Center's modernization (25-039, county keeps it) has neither.
_CHOW_TEXT_RE = re.compile(r"pre-transaction|post-transaction|change of ownership|transfer of ownership", re.I)


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def _project_year(code: str) -> int:
    """'25-031' -> 2025; 'E-031-24' -> 2024."""
    m = re.match(r"(?i)e-\d{3}-(\d{2})$", code) or re.match(r"(\d{2})-\d{3}$", code)
    return 2000 + int(m.group(1))


def list_projects(index_html: str) -> list[dict]:
    seen, out = set(), []
    for href, code, label in _PROJECT_RE.findall(index_html):
        url = urljoin(CON_IL_INDEX_URL, href)
        if url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "code": code.upper(), "title": _clean(label)})
    return out


def is_ltc_title(title: str) -> bool:
    return bool(_LTC_TITLE_RE.search(title)) and not _NOT_LTC_RE.search(title)


def project_documents(page_html: str, page_url: str) -> list[str]:
    pdfs = [urljoin(page_url, h) for h in re.findall(r'href="([^"]+\.pdf)"', page_html, re.I)]
    return [p for p in pdfs if "/projectdocuments/" in p]


def is_chow_text(text: str) -> bool:
    return bool(_CHOW_TEXT_RE.search(text or ""))


def fetch_con_il_projects(is_known) -> tuple[list[dict], list[dict]]:
    """(articles, skipped). Articles have raw_text filled, ready for Claude
    extraction: nursing facility change-of-ownership projects not stored
    yet. Skipped are nursing facility projects that turned out not to be
    sales (modernizations, new builds); the caller stores them so their
    scanned applications aren't transcribed again."""
    try:
        projects = list_projects(_get(CON_IL_INDEX_URL).text)
    except Exception as e:
        logger.error(f"IL CON index fetch failed: {e}")
        health.source_failed("IL CON projects", f"{CON_IL_INDEX_URL}: {e}")
        return [], []

    min_year = datetime.now().year - YEARS_BACK
    candidates = [p for p in projects if is_ltc_title(p["title"]) and _project_year(p["code"]) >= min_year
                  and not is_known(p["url"])]
    articles, skipped = [], []
    for p in candidates:
        try:
            docs = project_documents(_get(p["url"]).text, p["url"])
        except Exception as e:
            logger.warning(f"IL CON {p['code']} page fetch failed: {e}")
            continue
        if not docs:
            continue  # documents not posted yet; check again next run
        # The application: a document named as a change of ownership, else the first one
        app = next((d for d in docs if _CHOW_DOC_RE.search(unquote(d))), docs[0])
        health.attempted("IL CON filing download")
        try:
            text, _ = pdf_text(_get(app, timeout=120).content, MAX_PDF_PAGES, p["title"])
        except Exception as e:
            logger.warning(f"IL CON {p['code']} download/parse failed: {e}")
            health.failed("IL CON filing download", f"{app}: {e}")
            continue
        name = unquote(app.rsplit("/", 1)[-1])
        filed = re.match(r"(\d{4}-\d{2}-\d{2}) ", name)
        published = (datetime.strptime(filed.group(1), "%Y-%m-%d") if filed else datetime.now()).replace(tzinfo=timezone.utc)
        article = {"url": p["url"], "title": f"[IL CON] {p['title']}", "published_at": published,
                   "source_type": "con"}
        if not (_CHOW_DOC_RE.search(name) or is_chow_text(text)):
            skipped.append({**article, "raw_text": text, "skip_reason": "IL CON: not a change of ownership"})
            continue
        article["raw_text"] = (
            f"Illinois Health Facilities and Services Review Board project {p['code']}, "
            f"\"{p['title']}\": a change-of-ownership application for a nursing facility, "
            f"filed with the Board for approval before the sale closes, i.e. current news. "
            f"Report it as ONE deal: the applicant/buyer, the current owner/seller (the "
            f"pre-transaction owner or licensee), the facility, and acquisition_date = the "
            f"expected closing date if stated, else null.\n\nText of {name}:\n\n{text}"
        )
        articles.append(article)
    logger.info(f"IL CON: {len(projects)} projects listed, {len(candidates)} new nursing facility projects, "
                f"{len(articles)} changes of ownership, {len(skipped)} other projects")
    return articles, skipped
