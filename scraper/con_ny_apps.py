"""
New York DOH Certificate of Need applications (health.data.ny.gov h343-jwie)
-- nursing home ownership changes from the day the application is received.

DOH's open-data copy of its CON tracking system lists every application
with the current and the proposed operator and a one-line description,
updated daily. For a nursing home (facility type "Residential Health Care
Facility") two submission types are ownership changes:

  Application - Full Review - Establishment - Change in Ownership
      Either a new operator ("Establish Amsterdam Operations Associates, LLC
      as the new operator of ...") or members of the same operator trading
      shares ("Transfer 90.1% ownership interest from one withdrawing
      member to the sole remaining member").
  Notice - Transfer of Ownership Interest
      Smaller interest moves that need only a notice (1-20% typically).

Kept (user, 2026-11-09): a new operator always; an interest transfer only
when it moves 50% or more (control changes hands). Withdrawn and
"established in error" applications are dropped.

These are months earlier than the PHHPC committee agenda that con_ny.py
reads (the same project number reaches the agenda once DOH has reviewed
it). A project already stored from an agenda is skipped here, and
merge_into_phhpc() folds a record from here into the agenda's record when
the agenda comes later (the agenda has the price and the seller).

No Claude: the dataset's columns are the record. Homes are matched to CMS
by street address like the NY licence list (licence_lists.match_ccns).
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone

from curl_cffi import requests as cffi_requests

from pipeline.run_health import health
from scraper.licence_lists import match_ccns, same_entity

logger = logging.getLogger(__name__)

CON_NY_APPS_SOURCE_NAME = "New York DOH Certificate of Need Applications (open data)"
CON_NY_APPS_INDEX_URL = "https://health.data.ny.gov/Health/Certificate-of-Need-Applications-Beginning-1974/h343-jwie"
API_URL = "https://health.data.ny.gov/resource/h343-jwie.json"

RECENCY_DAYS = 365
MIN_INTEREST_PCT = 50.0
DROP_STATUSES = {"Withdrawn", "Established in Error", "Project Superceded"}
DONE_STATUSES = {"Project Complete", "Partial Completion"}

_NEW_OPERATOR_RE = re.compile(r"\bas the (new )?operator\b|\bnew operator\b", re.I)
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:of the\s+)?(?:ownership|membership|interest|stock|shares)", re.I)


def facility_name(name: str) -> str:
    """'Woodside Manor Nursing Home Inc' -> 'Woodside Manor Nursing Home': the
    dataset gives some homes their corporate name, which reads as a company."""
    return re.sub(r",?\s+(Inc\.?|LLC|L\.L\.C\.|Corp\.?|Corporation)$", "", name.strip(), flags=re.I).strip()


def record_url(project: str) -> str:
    return f"{CON_NY_APPS_INDEX_URL}#project-{project}"


def interest_pct(description: str) -> float | None:
    """Largest percentage moved in the description (some name two transfers)."""
    pcts = [float(p) for p in _PCT_RE.findall(description or "")]
    return max(pcts) if pcts else None


def classify(row: dict) -> str | None:
    """'new_operator', 'interest' (>= 50%) or None (not kept)."""
    if row.get("facility_type") != "Residential Health Care Facility":
        return None
    kind = row.get("submission_type_s") or ""
    if "Change in Ownership" not in kind and "Transfer of Ownership Interest" not in kind:
        return None
    if row.get("project_status") in DROP_STATUSES:
        return None
    desc = row.get("project_description") or ""
    current, proposed = row.get("current_operator_name") or "", row.get("proposed_operator_name") or ""
    if "Change in Ownership" in kind and (
            _NEW_OPERATOR_RE.search(desc) or (proposed and current and not same_entity(current, proposed))):
        return "new_operator"
    pct = interest_pct(desc)
    return "interest" if pct is not None and pct >= MIN_INTEREST_PCT else None


def fetch_rows(since: date) -> list[dict]:
    where = ("facility_type='Residential Health Care Facility' AND "
             "(submission_type_s like '%Change in Ownership%' OR submission_type_s like '%Transfer of Ownership Interest%') "
             f"AND application_received_date >= '{since.isoformat()}T00:00:00'")
    resp = cffi_requests.get(API_URL, params={"$where": where, "$order": "application_received_date", "$limit": 5000},
                             impersonate="chrome", timeout=120)
    resp.raise_for_status()
    return resp.json()


def to_deal(row: dict, kind: str) -> dict:
    project = row["con_project_number"]
    received = datetime.fromisoformat(row["application_received_date"][:10]).date()
    facility = facility_name(row.get("facility_name") or row.get("main_site_facility_name") or "")
    current = (row.get("current_operator_name") or "").strip()
    proposed = (row.get("proposed_operator_name") or "").strip()
    desc = (row.get("project_description") or "").strip().rstrip(".")
    status = row.get("project_status") or "status not stated"
    where = ", ".join(x for x in (row.get("main_site_address_line1"), row.get("main_site_city")) if x)
    if kind == "new_operator":
        title = f"[NY CON] {project}: {proposed or 'new operator'} to take over {facility}"
        summary = (f"New York DOH Certificate of Need application {project}, received {received:%B %-d, %Y}: "
                   f"{desc}. Current operator: {current or 'not stated'}; proposed operator: "
                   f"{proposed or 'not stated'}. Status: {status}. "
                   + ("DOH marks the project complete, so the new operator has taken over. "
                      if status in DONE_STATUSES else
                      "A new operator needs PHHPC approval, so this is a pending sale. ")
                   + "The application gives no price.")
        buyer, seller = proposed or None, current or None
    else:
        pct = interest_pct(desc)
        title = f"[NY CON] {project}: {pct:g}% ownership interest moves at {facility}"
        summary = (f"New York DOH Certificate of Need application {project}, received {received:%B %-d, %Y}: "
                   f"{desc} in {current or facility}, the operator of {facility}. The operator stays the same; "
                   f"the members behind it change. Status: {status}. The dataset doesn't name the members.")
        buyer, seller = None, None
    if where:
        summary += f" Facility address: {where}, NY."
    return {
        "url": record_url(project),
        "title": title[:250],
        "published_at": datetime.combine(received, datetime.min.time()).replace(tzinfo=timezone.utc),
        "raw_text": summary,
        "source_type": "con",
        "pre_extracted": True,
        "extraction_model": "con_direct",
        "_con_id": f"NY-{project}",
        "acquiring_entity": buyer,
        "seller_entity": seller,
        "operator_names": [n for n in dict.fromkeys([proposed, current]) if n],
        "facility_names": [facility] if facility else [],
        "states": ["NY"],
        "facility_count": 1,
        "deal_value_m": None,
        "acquisition_date": None,
        "financing_amount_m": None,
        "lender": None,
        "rationale": summary,
        **({"ccns": [row["ccn"]]} if row.get("ccn") else {}),
    }


def fetch_con_ny_app_deals(is_known, phhpc_projects: set[str], cms_homes, today: date | None = None) -> list[dict]:
    """cms_homes: (provider_name, provider_address, provider_city, ccn) of NY homes."""
    today = today or date.today()
    try:
        rows = fetch_rows(today - timedelta(days=RECENCY_DAYS))
    except Exception as e:
        logger.error(f"NY CON applications fetch failed: {e}")
        health.source_failed("NY CON applications (open data)", f"{API_URL}: {e}")
        return []
    kept = []
    for row in rows:
        kind = classify(row)
        project = row.get("con_project_number")
        if not kind or not project or project in phhpc_projects or is_known(record_url(project)):
            continue
        row["_kind"] = kind
        row["facility"] = row.get("facility_name") or row.get("main_site_facility_name") or ""
        row["address"] = row.get("main_site_address_line1") or ""
        row["city"] = row.get("main_site_city") or ""
        kept.append(row)
    match_ccns(kept, cms_homes)
    deals = [to_deal(row, row["_kind"]) for row in kept]
    logger.info(f"NY CON applications: {len(rows)} nursing home ownership filings in {RECENCY_DAYS} days, "
                f"{len(deals)} new kept ({sum(1 for r in kept if r['_kind'] == 'new_operator')} new operators)")
    return deals


def merge_into_phhpc(conn) -> int:
    """A project first stored from the dataset and later from a PHHPC agenda:
    keep the agenda record (price, seller), fold the dataset one into it and
    keep the earlier first-seen date."""
    with conn.cursor() as cur:
        cur.execute(r"""
            SELECT ds.id, ag.id FROM deals ds
            JOIN articles dsa ON dsa.id = ds.article_id
            JOIN articles aga ON aga.url LIKE '%%public_health_and_health_planning_council%%#project-'
                                 || substring(dsa.url from '#project-(\d+)$')
            JOIN deals ag ON ag.article_id = aga.id
            WHERE dsa.url LIKE %s AND ds.merged_into IS NULL AND ag.merged_into IS NULL
              AND ds.stage <> 'dismissed' AND ag.stage <> 'dismissed'
        """, (CON_NY_APPS_INDEX_URL + "#project-%",))
        pairs = cur.fetchall()
        for drop, keep in pairs:
            cur.execute("UPDATE deals SET stage = 'dismissed', merged_into = %s, updated_at = NOW() WHERE id = %s",
                        (keep, drop))
            cur.execute("""UPDATE deals SET created_at = LEAST(created_at, (SELECT created_at FROM deals WHERE id = %s)),
                           updated_at = NOW() WHERE id = %s""", (drop, keep))
    conn.commit()
    return len(pairs)
