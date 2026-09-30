"""
Oklahoma OSDH "The Notice" -- monthly Certificate of Need status report.

63 O.S. §1-852 requires a CON to acquire an existing long-term care
facility, and §1-857(B) requires OSDH to post a monthly report of active CON
projects. Each issue is a 2-page PDF table: CN #, facility, application
received date, application type, status. No buyer or seller is published,
so these become facility-only deals ("someone is acquiring X"); the value is
the lead time -- an acquisition CN is filed before closing, so it shows up
months ahead of CMS CHOW. See docs/con-feasibility.md (Oklahoma section).

Issues are linked from the Health Facility Systems page (filenames are
inconsistent -- "July Notice 2026.pdf", "AugustNotice2026wl.pdf",
"10082025 September Notice 2025 Final.pdf" -- so they can't be guessed).
Each issue lists every project still *active*, so the same CN repeats month
to month with an updated status; issues are parsed newest first and the
first sighting of each CN wins. The table is regular enough to parse
without Claude, so these take the pre_extracted path like CHOW.

Only ownership-change types are kept. The CN suffix encodes the exemption
form: -372 is a change-of-ownership/stock-transfer exemption (kept); -812
management agreements, -371 relocations and -372B bed expansions are not
ownership changes (skipped), nor is new construction or standard review.
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

CON_OK_SOURCE_NAME = "Oklahoma OSDH Certificate of Need Notice"
CON_OK_INDEX_URL = "https://oklahoma.gov/health/services/licensing-inspections/long-term-care-service/health-facility-systems.html"

# Each issue lists all active projects, so the latest ~year of issues covers
# anything filed in the recency window.
ISSUES_TO_READ = 13
CON_OK_RECENCY_DAYS = 365

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}
_ISSUE_LINK_RE = re.compile(
    r'href="([^"]*/the-notice/(\d{4})/[^"]+\.pdf)"[^>]*>\s*([A-Za-z]+)\s*</a>', re.I)
# A row starts with its CN number on a fresh line: "26-048", "26-035-372", "26-054-372B"
_ROW_START_RE = re.compile(r"^(\d{2}-\d{3}(?:-\d{3}[A-Z]?)?)\b\s*", re.M)
_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_STATUS_RE = re.compile(
    r"\b(Issued|Under|Postponed|Withdrawn|Denied|Approved|Pending|Returned|Deemed|Dismissed|Expired)\b.*$",
    re.I | re.S)
# 2025 issues word it "Change for Ownership", 2026 ones "Change of Ownership"
_OWNERSHIP_TYPE_RE = re.compile(r"acquisition|change (?:of|for) ownership|stock transfer", re.I)
# Withdrawn applications are usually refiled under a new CN (25-015..019 ->
# 26-018..022), so keeping them would double-count the same transaction
_DEAD_STATUS_RE = re.compile(r"^(withdrawn|denied|dismissed|expired)", re.I)
_NON_OWNERSHIP_SUFFIXES = ("-812", "-371", "-372B")


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def _issue_urls(html: str) -> list[tuple[date, str]]:
    """(issue month, absolute PDF url), newest first."""
    issues = {}
    for href, year, month_name in _ISSUE_LINK_RE.findall(html):
        month = _MONTHS.get(month_name.lower())
        if not month:
            continue  # e.g. the combined "2024 The Notice" archive
        # One index link is missing its leading slash
        href = href if href.startswith(("http", "/")) else "/" + href
        issues[date(int(year), month, 1)] = urljoin(CON_OK_INDEX_URL, href)
    return sorted(issues.items(), reverse=True)


def _parse_rows(text: str) -> list[dict]:
    starts = list(_ROW_START_RE.finditer(text))
    rows = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        body = re.sub(r"\s+", " ", text[m.end():end]).strip()
        d = _DATE_RE.search(body)
        if not d:
            continue
        rest = body[d.end():].strip()
        status = _STATUS_RE.search(rest)
        rows.append({
            "cn": m.group(1),
            "facility": body[:d.start()].strip(" ,"),
            "received": date(int(d.group(3)), int(d.group(1)), int(d.group(2))),
            "app_type": (rest[:status.start()] if status else rest).strip(),
            "status": status.group(0).strip() if status else "",
        })
    return rows


def _is_ownership_change(row: dict) -> bool:
    if row["cn"].endswith(_NON_OWNERSHIP_SUFFIXES) or _DEAD_STATUS_RE.match(row["status"]):
        return False
    return bool(_OWNERSHIP_TYPE_RE.search(row["app_type"]))


def record_url(cn: str) -> str:
    """Stable per-CN article URL (the issue PDF a CN appears in changes monthly)."""
    return f"{CON_OK_INDEX_URL}#cn-{cn}"


def fetch_con_ok_deals(is_known) -> list[dict]:
    """
    Return pre-extracted deal dicts for ownership-change CNs not yet stored.
    is_known(url) -> bool says whether the CN's record_url() already exists.
    """
    try:
        issues = _issue_urls(_get(CON_OK_INDEX_URL).text)
    except Exception as e:
        logger.error(f"OK CON index fetch failed: {e}")
        health.source_failed("OK CON notice", f"index fetch failed: {e}")
        return []
    if not issues:
        health.source_failed("OK CON notice", "index listed 0 issues (layout change?)")
        return []
    logger.info(f"OK CON: {len(issues)} issues listed, newest {issues[0][0]:%B %Y}")

    cutoff = date.today() - timedelta(days=CON_OK_RECENCY_DAYS)
    seen_cns = set()
    deals = []
    for issue_month, pdf_url in issues[:ISSUES_TO_READ]:
        health.attempted("OK CON issue parse")
        try:
            reader = PdfReader(io.BytesIO(_get(pdf_url).content))
            rows = _parse_rows("\n".join((p.extract_text() or "") for p in reader.pages))
        except Exception as e:
            logger.warning(f"OK CON {issue_month:%B %Y} download/parse failed: {e}")
            health.failed("OK CON issue parse", f"{pdf_url}: {e}")
            continue
        if not rows:
            # Every issue so far has had rows; zero means the layout changed
            health.failed("OK CON issue parse", f"{pdf_url}: 0 rows parsed")
            continue

        for row in rows:
            if row["cn"] in seen_cns:
                continue
            seen_cns.add(row["cn"])
            if not _is_ownership_change(row) or row["received"] < cutoff:
                continue
            url = record_url(row["cn"])
            if is_known(url):
                continue

            summary = (
                f"Oklahoma CON {row['cn']}: {row['app_type']} for {row['facility']}, "
                f"application received {row['received']:%m/%d/%Y}. Status as of the "
                f"{issue_month:%B %Y} Notice: {row['status'] or 'not stated'}. "
                f"OSDH does not publish the buyer or seller."
            )
            deals.append({
                "url": url,
                "title": f"[OK CON] {row['cn']} {row['app_type']}: {row['facility']}",
                "published_at": datetime.combine(row["received"], datetime.min.time()).replace(tzinfo=timezone.utc),
                "raw_text": f"{summary}\n\nSource: {pdf_url}",
                "source_type": "con",
                "pre_extracted": True,
                "extraction_model": "con_direct",
                "_con_id": row["cn"],
                "acquiring_entity": None,
                "seller_entity": None,
                "operator_names": [],
                "facility_names": [row["facility"]],
                "states": ["OK"],
                "facility_count": 1,
                "deal_value_m": None,
                # Filing date -- the closing date isn't published
                "acquisition_date": row["received"].isoformat(),
                "financing_amount_m": None,
                "lender": None,
                "rationale": summary,
            })

    logger.info(f"OK CON: {len(deals)} new ownership-change CNs")
    return deals
