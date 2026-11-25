"""
Washington DOH Certificate of Need projects -- hospice and rehab hospital
ownership changes.

Buying an existing hospice agency or hospital in Washington goes past the CN
Program: a hospital sale needs a CN, and a hospice sale an exemption request
("DOR", a determination of reviewability: "Requesting exemption from CN
review for the transfer of LLC membership interest from ... to ..."). The
Program's Project Status page lists every project since 2019 and offers it
as one CSV (admin/certificate-of-need-export.csv, ~1,000 rows): applicant,
project type, county, stage, a one-line description, dates.

Few rows are sales (2021-2026: 4 hospice, 0 inpatient rehab), and project
types are sometimes wrong (Heart to Heart Hospice's 2026 transfer is filed
as "Ambulatory surgery"), so a row is kept when the description is an
ownership change and the applicant, description or type says hospice or
rehab hospital. Nursing home sales need no CN in Washington; they come from
the licence-number diff (licence_lists.py). Parsed without Claude.
Hospice and rehab in scope since 2026-11-25.
"""

import csv
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone

from curl_cffi import requests as cffi_requests

from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_WA_SOURCE_NAME = "Washington DOH Certificate of Need Projects"
CON_WA_INDEX_URL = ("https://doh.wa.gov/licenses-permits-and-certificates/facilities-z/"
                    "certificate-need/project-status")
CSV_URL = "https://doh.wa.gov/admin/certificate-of-need-export.csv"
RECENCY_DAYS = 365

_OWNERSHIP_RE = re.compile(r"\bsale\b|purchase|acqui|change (?:of|in) (?:the )?ownership|membership interest"
                           r"|transfer (?:of )?(?:the |their )?(?:CON|CN|ownership|LLC)", re.I)
_SCOPE_RE = re.compile(r"hospice|rehabilitation hospital|inpatient rehab", re.I)
# the last "from <seller> to <buyer>" ("exemption from CN review for the
# transfer ... from Heart'n Soul of Seattle to Heart to Heart Hospice")
_FROM_TO_RE = re.compile(r"\bfrom (?P<seller>(?:(?!\bfrom\b).){3,80}?) to (?P<buyer>[^.,]{3,80}?)\s*(?:\.|,| in | for |$)", re.I)


def fetch_rows() -> list[dict]:
    resp = cffi_requests.get(CSV_URL, params={"type": "All", "items_per_page": "All"},
                             impersonate="chrome", timeout=120)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def _date(s: str):
    try:
        return datetime.strptime((s or "").strip()[:10], "%m/%d/%Y").date()
    except ValueError:
        return None


def is_kept(row: dict) -> bool:
    desc = row.get("Project Description") or ""
    text = " ".join([row.get("Applicant") or "", desc, row.get("Project Type") or ""])
    return bool(_OWNERSHIP_RE.search(desc)) and bool(_SCOPE_RE.search(text))


def record_url(project: str) -> str:
    return f"{CON_WA_INDEX_URL}#project-{project}"


def to_deal(row: dict, filed: date) -> dict:
    project = row["CoN Project Number"].strip()
    applicant = (row.get("Applicant") or "").strip()
    desc = " ".join((row.get("Project Description") or "").split()).rstrip(".")
    county = (row.get("County") or "").strip()
    decision = " ".join((row.get("CoN Evaluation and Decision Text") or "").split())
    m = ([*_FROM_TO_RE.finditer(desc)] or [None])[-1]
    buyer = m.group("buyer").strip() if m else applicant
    seller = m.group("seller").strip() if m and "change of ownership" not in m.group("seller") else None
    summary = (f"Washington DOH Certificate of Need project {project}, received {filed:%B %-d, %Y}"
               + (f" ({county} County)" if county and county != "All counties" else "")
               + f": {applicant}. {desc}. "
               + (f"Decision: {decision}." if decision else "No decision posted yet, so this is pending.")
               + (f" Application: {row['CoN Application Document']}." if row.get("CoN Application Document") else ""))
    return {
        "url": record_url(project),
        "title": f"[WA CN] {project}: {applicant}",
        "published_at": datetime.combine(filed, datetime.min.time()).replace(tzinfo=timezone.utc),
        "raw_text": summary,
        "source_type": "con",
        "pre_extracted": True,
        "extraction_model": "con_direct",
        "_con_id": f"WA-{project}",
        "acquiring_entity": buyer or None,
        "seller_entity": seller,
        "operator_names": [n for n in dict.fromkeys([buyer, seller]) if n],
        # The agency being bought is the seller's when the description names
        # one ("from Heart'n Soul of Seattle to Heart to Heart Hospice")
        "facility_names": [seller] if seller else [applicant] if applicant else [],
        "states": ["WA"],
        "facility_count": 1,
        "deal_value_m": None,
        "acquisition_date": None,
        "financing_amount_m": None,
        "lender": None,
        "rationale": summary,
    }


def fetch_con_wa_deals(is_known, today: date | None = None) -> list[dict]:
    today = today or date.today()
    try:
        rows = fetch_rows()
    except Exception as e:
        logger.error(f"WA CN projects fetch failed: {e}")
        health.source_failed("WA CN projects", f"{CSV_URL}: {e}")
        return []
    if not rows:
        health.source_failed("WA CN projects", "CSV export parsed to 0 rows (layout change?)")
        return []
    deals = []
    for row in rows:
        project = (row.get("CoN Project Number") or "").strip()
        filed = _date(row.get("CoN Application Received")) or _date(row.get("CoN LOI Received"))
        if (not project or not filed or filed < today - timedelta(days=RECENCY_DAYS)
                or not is_kept(row) or is_known(record_url(project))):
            continue
        deals.append(to_deal(row, filed))
    logger.info(f"WA CN: {len(rows)} projects listed, {len(deals)} new hospice / rehab hospital ownership changes")
    return deals
