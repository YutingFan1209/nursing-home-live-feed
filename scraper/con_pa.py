"""
Pennsylvania DOH nursing home licence applications open for public comment.

28 Pa. Code §201.12a (in force since 2023-10) makes DOH post every
nursing home licence application -- new facilities and changes of
ownership -- for a 10-day public comment period before it decides. The
"Initial Provider Application Public Review" page lists them:

- Pending applications, each linked to the application itself
  (InitialApplication.aspx?ipaappid=N&IPA_PUBLIC=y): an ASP.NET form whose
  <input value>s hold the facility, applicant (new licensee), anticipated
  start date (the closing), previous licence and legal name, landlord and
  management company. Only change-of-ownership applications are kept.
- Recently decided applications: posted date, new name, previous name,
  address, county and status, with no link.

The page is a rolling window: an application leaves the pending table
when its comment period ends and decided ones drop off after a few weeks
(the 2026-09-21 Cedar Haven and Park Avenue applications and the
2026-07-02 approvals were gone by 2026-10-05). Runs shouldn't lapse more
than a week. Application pages stay reachable by ID after they leave the
list, so known IDs can be backfilled (BACKFILL_APP_IDS).

Parsed without Claude (pre_extracted, like OK/MS). See
docs/con-feasibility.md "Remaining states", Pennsylvania detail.
"""

import logging
import re
from datetime import date, datetime, timezone

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_PA_SOURCE_NAME = "Pennsylvania DOH Nursing Home Licence Applications"
CON_PA_INDEX_URL = "https://sais.health.pa.gov/CommonPOC/Licensing/IPA-NCF/PublicReviewFacList.aspx"
_APP_URL = "https://sais.health.pa.gov/CommonPOC/Licensing/IPA-NCF/InitialApplication.aspx?ipaappid={}&IPA_PUBLIC=y"

# Seen on the list 2026-09-30, gone from it by 2026-10-05
BACKFILL_APP_IDS = (6813083, 6827525)

_FIELD = "ContentPlaceHolder1_"


def _get(url: str):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=60)
    resp.raise_for_status()
    return resp


def _date(s: str):
    try:
        return datetime.strptime((s or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def _norm(name: str) -> str:
    name = re.sub(r"[^A-Z0-9]+", " ", (name or "").upper())
    return " ".join(re.sub(r"\b(LLC|INC|THE)\b", " ", name).split())


def app_record_url(app_id) -> str:
    return _APP_URL.format(app_id)


def decision_record_url(posted: str, name: str) -> str:
    return f"{CON_PA_INDEX_URL}#decided-{posted.replace('/', '-')}-{re.sub(r'[^A-Za-z0-9]+', '-', name).strip('-')}"


def _parse_list(html: str) -> tuple[dict, list[dict]]:
    """(pending app id -> posted date string, decided rows)."""
    soup = BeautifulSoup(html, "html.parser")
    pending = {}
    for a in soup.find_all("a", href=re.compile(r"ipaappid=\d+")):
        app_id = int(re.search(r"ipaappid=(\d+)", a["href"]).group(1))
        row = a.find_parent("tr")
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")] if row else []
        posted = next((c for c in cells if re.fullmatch(r"\d{2}/\d{2}/\d{4}", c)), "")
        pending.setdefault(app_id, posted)
    decided = []
    table = soup.find(id=f"{_FIELD}HistoryTable")
    for tr in (table.find_all("tr")[1:] if table else []):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) >= 6 and _date(cells[0]):
            decided.append(dict(zip(("posted", "name", "previous_name", "address", "county", "status"), cells[:6])))
    return pending, decided


def _parse_application(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    def val(key):
        el = soup.find(id=_FIELD + key)
        return (el.get("value") or "").strip() if el else ""

    def checked(key):
        el = soup.find(id=_FIELD + key)
        return bool(el and el.has_attr("checked"))

    return {
        "facility": val("FacName"), "city": val("City"), "county": val("County"),
        "applicant": val("ApplName") or val("ApplicantName"),
        "start_date": _date(val("StartDate")) or _date(val("CHOWFutureDate")),
        "is_chow": checked("AppTypeCHOW"),
        "previous_license": val("CHOWLicNo"), "previous_name": val("CHOWLegalName"),
        "beds": val("BedCapacity"), "landlord": val("LeasedName"),
        "landlord_state": val("LeasedState"), "manager": val("ManagementName"),
    }


def _application_deal(app_id: int, posted: str, app: dict) -> dict:
    url = app_record_url(app_id)
    posted_d = _date(posted)
    seller = app["previous_name"] if _norm(app["previous_name"]) != _norm(app["applicant"]) else None
    when = app["start_date"] or posted_d or date.today()
    summary = (
        f"Pennsylvania DOH change-of-ownership licence application {app_id} for {app['facility']}"
        f" ({app['city']}, {app['county']} County, {app['beds'] or '?'} beds)"
        f"{', posted for public comment ' + posted if posted else ''}. Applicant (new licensee): "
        f"{app['applicant']}. Anticipated start of operation: "
        f"{app['start_date'].strftime('%m/%d/%Y') if app['start_date'] else 'not stated'}. "
        f"Previous licence {app['previous_license'] or '?'} ({app['previous_name'] or 'name not stated'})."
        + (f" Building leased from {app['landlord']}" + (f" ({app['landlord_state']})" if app['landlord_state'] else "") + "." if app["landlord"] else "")
        + (f" Managed by {app['manager']}." if app["manager"] else "")
    )
    return {
        "url": url,
        "title": f"[PA licence CHOW] {app['facility']}"
                 + (f" → new licensee {app['applicant']}" if _norm(app['applicant']) != _norm(app['facility']) else ""),
        "published_at": datetime.combine(posted_d or when, datetime.min.time()).replace(tzinfo=timezone.utc),
        "raw_text": summary,
        "source_type": "con",
        "pre_extracted": True,
        "extraction_model": "con_direct",
        "_con_id": f"app-{app_id}",
        "acquiring_entity": app["applicant"],
        "seller_entity": seller,
        "operator_names": [n for n in (app["applicant"], app["landlord"], app["manager"]) if n],
        "facility_names": [app["facility"]],
        "states": ["PA"],
        "facility_count": 1,
        "deal_value_m": None,
        # The anticipated start of operation is the planned closing
        "acquisition_date": when.isoformat(),
        "financing_amount_m": None,
        "lender": None,
        "rationale": summary,
    }


def _decision_deal(row: dict) -> dict:
    posted_d = _date(row["posted"])
    summary = (
        f"Pennsylvania DOH licence application for {row['name']} (previously {row['previous_name']}), "
        f"{row['address']}, {row['county']} County: {row['status']}. Posted for public comment "
        f"{row['posted']}. The decided list doesn't publish the applicant."
    )
    return {
        "url": decision_record_url(row["posted"], row["name"]),
        "title": f"[PA licence] {row['previous_name']} → {row['name']} ({row['status']})",
        "published_at": datetime.combine(posted_d, datetime.min.time()).replace(tzinfo=timezone.utc),
        "raw_text": summary,
        "source_type": "con",
        "pre_extracted": True,
        "extraction_model": "con_direct",
        "_con_id": f"decided-{row['posted']}-{row['name']}",
        "acquiring_entity": None,
        "seller_entity": None,
        "operator_names": [],
        "facility_names": [row["name"]] + ([row["previous_name"]] if _norm(row["previous_name"]) != _norm(row["name"]) else []),
        "states": ["PA"],
        "facility_count": 1,
        "deal_value_m": None,
        "acquisition_date": posted_d.isoformat(),
        "financing_amount_m": None,
        "lender": None,
        "rationale": summary,
    }


def fetch_con_pa_deals(is_known, known_facilities=frozenset(), extra_app_ids=BACKFILL_APP_IDS) -> list[dict]:
    """Pre-extracted deals for change-of-ownership applications and decided
    rows not yet stored. is_known(url) -> bool. known_facilities holds
    normalized facility names of PA licence deals already stored: an
    approved application reappears in the decided table, and shouldn't
    become a second deal."""
    try:
        pending, decided = _parse_list(_get(CON_PA_INDEX_URL).text)
    except Exception as e:
        logger.error(f"PA CON list fetch failed: {e}")
        health.source_failed("PA CON licence applications", f"list fetch failed: {e}")
        return []
    if not pending and not decided:
        # Possible between batches, but also what a layout change looks like
        health.note("PA CON: public review list had no pending or decided rows")

    deals = []
    app_ids = dict(pending)
    for app_id in extra_app_ids:
        app_ids.setdefault(app_id, "")
    for app_id, posted in app_ids.items():
        if is_known(app_record_url(app_id)):
            continue
        health.attempted("PA CON application parse")
        try:
            app = _parse_application(_get(app_record_url(app_id)).text)
        except Exception as e:
            health.failed("PA CON application parse", f"{app_id}: {e}")
            continue
        if not app["facility"]:
            health.failed("PA CON application parse", f"{app_id}: no facility name (layout change?)")
            continue
        if app["is_chow"]:
            deals.append(_application_deal(app_id, posted, app))

    seen = set(known_facilities) | {_norm(d["facility_names"][0]) for d in deals}
    for row in decided:
        if is_known(decision_record_url(row["posted"], row["name"])):
            continue
        if any(_norm(n) in seen for n in (row["name"], row["previous_name"])):
            continue
        deals.append(_decision_deal(row))

    logger.info(f"PA CON: {len(pending)} pending, {len(decided)} decided on the list -> {len(deals)} new")
    return deals
