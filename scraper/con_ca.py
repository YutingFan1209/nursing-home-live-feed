"""
California OHCA material change transaction notices -- skilled nursing deals.

Health & Safety Code §127507 (SB 184) makes health care entities above
OHCA's revenue thresholds file a notice at least 90 days before closing a
material change: a sale, merger or transfer of control. OHCA lists every
notice in one HTML table (parties, a one-paragraph description of the
transaction, submission date, CMIR status) linked to the notice PDF(s).
The parties and description usually name buyer and seller, months before
CMS CHOW. Low volume, about one notice a month across all health care,
roughly one in five a skilled nursing deal. Only mid-size and larger
operators are covered, not every sale. See docs/con-feasibility.md
(California detail).

Most notices are hospitals, labs and physician groups, so a notice is
kept if its row text names a skilled nursing facility, or, failing that,
its notice PDF's text layer does repeatedly (Covenant Care's row only
says "facilities"). Hospital and diagnostics notices mention SNFs in
passing up to ~5 times, real SNF deals 7+ (checked 2026-10-06). Notices
that fail both are returned as skipped so their PDFs aren't fetched again.
"""

import io
import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from pypdf import PdfReader

from pipeline.pdf_text import pdf_text
from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_CA_SOURCE_NAME = "California OHCA Material Change Transaction Notices"
CON_CA_INDEX_URL = ("https://hcai.ca.gov/affordability/ohca/assess-market-consolidation/"
                    "material-change-transaction-notices-mcn-and-cost-and-market-impact-review-cmir/")

MAX_PDF_PAGES = 8
# Text-layer pages scanned when the row itself doesn't mention a SNF
_SCREEN_PAGES = 6
# SNF keyword hits a notice PDF needs when the row text has none
MIN_PDF_SNF_HITS = 6

_SNF_RE = re.compile(
    r"skilled nursing|nursing (?:facilit|home|center)|\bSNFs?\b|post-acute|care cent(?:er|re)"
    r"|convalescent|rehab(?:ilitation)? (?:&|and )?(?:health ?care )?cent",
    re.I,
)


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def _parse_notices(page_html: str) -> list[dict]:
    soup = BeautifulSoup(page_html, "html.parser")
    table = soup.find("table")
    notices = []
    for tr in (table.find_all("tr")[1:] if table else []):
        cells = tr.find_all("td")
        if len(cells) < 3:
            continue
        text = [c.get_text(" ", strip=True) for c in cells]
        try:
            submitted = datetime.strptime(text[2], "%B %d, %Y").date()
        except ValueError:
            submitted = None
        links = [a["href"] for a in cells[0].find_all("a", href=True)]
        if not links:
            continue
        notices.append({
            "parties": text[0], "description": text[1], "submitted": submitted,
            "cmir": text[3] if len(text) > 3 else "", "links": links,
        })
    return notices


def _pdf_snf_hits(content: bytes) -> int:
    reader = PdfReader(io.BytesIO(content))
    text = " ".join((p.extract_text() or "") for p in reader.pages[:_SCREEN_PAGES])
    return len(_SNF_RE.findall(text))


def fetch_con_ca_notices(is_known) -> tuple[list[dict], list[dict]]:
    """
    (articles, skipped). Articles carry raw_text for Claude extraction, one
    per SNF notice not yet stored, keyed on its first notice link. Skipped
    are non-SNF notices, to be stored with skip_reason so they're not
    downloaded again.
    """
    try:
        notices = _parse_notices(_get(CON_CA_INDEX_URL).text)
    except Exception as e:
        logger.error(f"CA OHCA list fetch failed: {e}")
        health.source_failed("CA OHCA notices", f"list fetch failed: {e}")
        return [], []
    if not notices:
        health.note("CA OHCA: notice table had no rows (layout change?)")

    articles, skipped = [], []
    for n in notices:
        url = n["links"][0]
        if is_known(url):
            continue
        published = datetime.combine(n["submitted"] or datetime.now(timezone.utc).date(),
                                     datetime.min.time()).replace(tzinfo=timezone.utc)
        base = {
            "url": url,
            "title": f"[CA OHCA] {n['parties'][:200]}",
            "published_at": published,
            "source_type": "con",
        }
        row_snf = bool(_SNF_RE.search(f"{n['parties']} {n['description']}"))

        health.attempted("CA OHCA notice download")
        try:
            content = _get(url, timeout=120).content
            if not row_snf and _pdf_snf_hits(content) < MIN_PDF_SNF_HITS:
                skipped.append({**base, "raw_text": n["description"],
                                "skip_reason": "CA OHCA: not a skilled nursing transaction"})
                continue
            text, _ = pdf_text(content, MAX_PDF_PAGES, n["parties"][:80])
        except Exception as e:
            logger.warning(f"CA OHCA {url} download/parse failed: {e}")
            health.failed("CA OHCA notice download", f"{url}: {e}")
            continue

        others = "".join(f"\n- {link}" for link in n["links"][1:])
        articles.append({
            **base,
            "raw_text": (
                f"California Office of Health Care Affordability (OHCA) material change "
                f"transaction notice, submitted {n['submitted'] or 'date not stated'}. OHCA "
                f"requires these at least 90 days before closing, so this is a pending "
                f"transaction, i.e. current news. Report it as ONE deal covering every "
                f"skilled nursing facility named, with acquisition_date = the expected "
                f"closing date if stated, else null. Ignore non-nursing facilities.\n\n"
                f"Parties: {n['parties']}\nTransaction: {n['description']}\n"
                f"Cost and market impact review: {n['cmir'] or 'not stated'}"
                + (f"\nOther notices filed for this transaction:{others}" if others else "")
                + f"\n\nText of the notice:\n\n{text}"
            ),
        })

    logger.info(f"CA OHCA: {len(notices)} notices listed, {len(articles)} new SNF, {len(skipped)} skipped")
    return articles, skipped
