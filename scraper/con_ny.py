"""
New York PHHPC establishment approvals -- nursing home changes of operator.

PHL §2801-a makes any new operator of a residential health care facility
(RHCF, New York's term for a nursing home / SNF) be "established" by the
Public Health and Health Planning Council. Every application goes first to
the Establishment and Project Review Committee (EPRC), then to a Full
Council vote, and both meetings' agenda PDFs carry the same exhibit for it:
"Project #NNNNNN-E Exhibit Page 1" names the applicant (buyer, with d/b/a),
the program, the facility and beds, the current and proposed owners with
member percentages, the purchase agreement date, the prices for operations
and real estate, and (on page 3) the Council Action Date. The committee
agenda is the earliest public signal: 14-28 days before the vote for all
9 of 9 nursing home sales in the 2025-10 to 2026-09 backtest
(docs/con-feasibility.md, "NY scraping test").

Only exhibits whose header says "Program: Residential Health Care Facility"
on an establishment-only project (-E suffix) are kept -- adult care
facilities, home care (LHCSA/CHHA), hospice, hospitals and D&TCs have their
own program lines, and -C (construction) and -B (establish and build a new
facility, e.g. 261111-B, a new 40-bed RHCF in 2026-05) aren't ownership
changes. A project is
keyed on its number, so its second appearance (the council agenda) is
skipped; meetings are read oldest first, so the committee date wins.

Meeting pages stay up for years (back to 2021), so a missed run loses
nothing as long as it's inside CON_NY_RECENCY_DAYS. Each scanned PDF is
returned as skipped so the 10-20 MB agendas aren't downloaded again.
The site 403s plain HTTP clients; curl_cffi's Chrome impersonation works.
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

CON_NY_SOURCE_NAME = "New York PHHPC Nursing Home Establishment Approvals"
CON_NY_INDEX_URL = "https://www.health.ny.gov/facilities/public_health_and_health_planning_council/"
CON_NY_RECENCY_DAYS = 365
# Pages 1-3 hold the parties, ownership tables, prices and Council Action Date
MAX_EXHIBIT_PAGES = 3

_MEETING_RE = re.compile(r'href="(/facilities/public_health_and_health_planning_council/meetings/'
                         r'(\d{4})-(\d{2})-(\d{1,2})[^/"]*/)"')
_PDF_LINK_RE = re.compile(r'<a [^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.S | re.I)
_TITLE_RE = re.compile(r'<h1 id="pagetitle">(.*?)</h1>', re.S)
# Not agendas: no exhibits in them
_SKIP_DOC_RE = re.compile(r"transcript|minutes|video|webcast", re.I)
_EXHIBIT_RE = re.compile(r"Project\s*#\s*(\d{6})-([A-Z]+)\s+Exhibit\s+Page\s+(\d+)")
_PROGRAM_RE = re.compile(r"Program:\s*(.*?)\s+County:\s*([A-Za-z .]+?)\s*\n", re.S)
_PURPOSE_RE = re.compile(r"Purpose:\s*([^\n]*)")
_ACTION_DATE_RE = re.compile(r"Council\s+Action\s+Date\s*\n?\s*([A-Z][a-z]+ \d{1,2}, \d{4})")
_PROJECT_URL_RE = re.compile(r"#project-(\d{6})$")


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def project_number(url: str) -> str | None:
    m = _PROJECT_URL_RE.search(url or "")
    return m.group(1) if m else None


def _parse_meetings(index_html: str) -> list[tuple[date, str]]:
    meetings = {}
    for path, y, m, d in _MEETING_RE.findall(index_html):
        try:
            meetings[urljoin(CON_NY_INDEX_URL, path)] = date(int(y), int(m), int(d))
        except ValueError:
            continue
    return sorted(((d, url) for url, d in meetings.items()), key=lambda x: x[0])


def _parse_date(s: str):
    try:
        return datetime.strptime(s, "%B %d, %Y").date()
    except (TypeError, ValueError):
        return None


def _rhcf_exhibits(content: bytes) -> list[dict]:
    """Nursing home establishment exhibits in one agenda PDF: project number,
    county, purpose, Council Action Date and the text of pages 1-3."""
    pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(content)).pages]
    exhibits = []
    for i, text in enumerate(pages):
        head = _EXHIBIT_RE.search(text[:300])
        if not head or head.group(3) != "1" or head.group(2) != "E":
            continue
        program = _PROGRAM_RE.search(text)
        purpose = _PURPOSE_RE.search(text)
        if not program or "residential health care facility" not in program.group(1).lower():
            continue
        if not purpose or "establishment" not in purpose.group(1).lower():
            continue
        number = head.group(1)
        body = [text]
        for nxt in pages[i + 1:i + MAX_EXHIBIT_PAGES]:
            h = _EXHIBIT_RE.search(nxt[:300])
            if not h or h.group(1) != number:
                break
            body.append(nxt)
        text_all = "\n".join(body)
        action = _ACTION_DATE_RE.search(text_all)
        exhibits.append({
            "number": number,
            "suffix": head.group(2),
            "county": program.group(2).strip(),
            "purpose": purpose.group(1).strip(),
            "action_date": _parse_date(action.group(1)) if action else None,
            "text": text_all,
        })
    return exhibits


def fetch_con_ny_projects(is_known, known_projects=frozenset()) -> tuple[list[dict], list[dict]]:
    """
    (articles, scanned). Articles carry raw_text for Claude extraction, one
    per nursing home establishment project not seen before, keyed on
    "<first agenda PDF>#project-<number>". Scanned are the agenda PDFs read
    this run, to be stored with skip_reason so they aren't downloaded again.
    """
    try:
        meetings = _parse_meetings(_get(CON_NY_INDEX_URL).text)
    except Exception as e:
        logger.error(f"NY PHHPC index fetch failed: {e}")
        health.source_failed("NY PHHPC establishments", f"index fetch failed: {e}")
        return [], []
    if not meetings:
        health.source_failed("NY PHHPC establishments", "index parsed to 0 meetings (layout change?)")
        return [], []

    today = date.today()
    cutoff = today - timedelta(days=CON_NY_RECENCY_DAYS)
    seen = set(known_projects)
    articles, scanned = [], []
    for meeting_date, meeting_url in meetings:
        if meeting_date < cutoff:
            continue
        try:
            page = _get(meeting_url).text
        except Exception as e:
            logger.warning(f"NY PHHPC meeting {meeting_url} fetch failed: {e}")
            health.failed("NY PHHPC meeting page", f"{meeting_url}: {e}")
            continue
        title = _TITLE_RE.search(page)
        meeting_title = _clean(title.group(1)) if title else f"PHHPC meetings of {meeting_date}"

        for href, label in _PDF_LINK_RE.findall(page):
            pdf_url, label = urljoin(meeting_url, href), _clean(label)
            if _SKIP_DOC_RE.search(f"{href} {label}") or is_known(pdf_url):
                continue
            health.attempted("NY PHHPC agenda download")
            try:
                exhibits = _rhcf_exhibits(_get(pdf_url, timeout=180).content)
            except Exception as e:
                logger.warning(f"NY PHHPC {pdf_url} download/parse failed: {e}")
                health.failed("NY PHHPC agenda download", f"{pdf_url}: {e}")
                continue

            new = [x for x in exhibits if x["number"] not in seen]
            for x in new:
                seen.add(x["number"])
                vote = x["action_date"]
                published = min(meeting_date, today)
                articles.append({
                    "url": f"{pdf_url}#project-{x['number']}",
                    "title": f"[NY PHHPC] Project {x['number']}-{x['suffix']}: nursing home establishment "
                             f"({x['county']} County)",
                    "published_at": datetime.combine(published, datetime.min.time()).replace(tzinfo=timezone.utc),
                    "raw_text": (
                        f"New York Public Health and Health Planning Council (PHHPC) application "
                        f"{x['number']}-{x['suffix']} to establish a new operator of a residential "
                        f"health care facility (nursing home), {x['county']} County, NY. It is on the "
                        f"agenda of {meeting_title} (\"{label}\"), "
                        f"with the Full Council vote {'scheduled for ' + vote.isoformat() if vote else 'date not stated'}. "
                        f"PHHPC approval is required before the new operator takes over, so this is a "
                        f"pending transaction, i.e. current news. Report it as ONE deal: "
                        f"acquiring_entity = the applicant (the proposed operator, not its d/b/a); "
                        f"seller_entity = the current operator; operator_names = the proposed "
                        f"operator's d/b/a and any parent or real estate buyer named; facility_names "
                        f"= the facility's current name, plus its new name if it will be renamed; "
                        f"deal_value_m = the operations purchase price plus the real estate price if "
                        f"stated; acquisition_date = "
                        + (f"{vote.isoformat()} (the Council Action Date). " if vote else "null. ")
                        + f"Ignore the budget and staffing detail.\n\n"
                        f"Text of the exhibit:\n\n{x['text']}"
                    ),
                    "source_type": "con",
                })
            scanned.append({
                "url": pdf_url,
                "title": f"[NY PHHPC] {meeting_title}: {label}",
                "published_at": datetime.combine(min(meeting_date, today), datetime.min.time())
                                        .replace(tzinfo=timezone.utc),
                "raw_text": "",
                "source_type": "con",
                "skip_reason": f"NY PHHPC agenda scanned: {len(exhibits)} nursing home establishment "
                               f"exhibit(s), {len(new)} new",
            })

    logger.info(f"NY PHHPC: {len(scanned)} agenda PDFs scanned, {len(articles)} new nursing home projects")
    return articles, scanned
