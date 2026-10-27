"""
Rhode Island Office of Health Systems Development -- Change in Effective
Control (CEC) applications for nursing homes.

A change in owner, operator or lessee of a licensed nursing facility needs
Health Services Council review and RIDOH approval before it takes effect
(R.I. Gen. Laws §23-17-14.3). OHSD keeps every pending and decided request
in a public Google Sheet ("Annual Report of Reviews"), one tab per year:
applicant, project description, filing / review / decision dates. The
applicant is the buyer, usually an opco + propco pair, and the description
names the home, its beds and the current licensee, months before closing.
See docs/con-feasibility.md (Rhode Island).

health.ri.gov answers scripts with 403; the sheet's CSV export doesn't.
The same application can appear in several year tabs, so the latest tab
wins. Many descriptions don't say "nursing" ("...d/b/a Grandview Center"),
so a row is a nursing home if it says so or names a CMS-listed RI home's
street address or name; home care, hospice, surgery and imaging requests
are dropped.
"""

import csv
import io
import logging
import re
from datetime import datetime, timezone

from curl_cffi import requests as cffi_requests

from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_RI_SOURCE_NAME = "Rhode Island OHSD Change in Effective Control Applications"
CON_RI_INDEX_URL = "https://health.ri.gov/health-systems/ridoh-programs/health-systems-development-office"
CON_RI_SHEET_ID = "1fdRP2rh34eluTT13k8EIqjJUSnGTH2U5yGD-KwRFi-M"
CON_RI_SHEET_URL = f"https://docs.google.com/spreadsheets/d/{CON_RI_SHEET_ID}/htmlview"
# One tab per year; newest first so a request listed again later wins
CON_RI_TABS = {2026: 833901994, 2025: 1595076143, 2024: 1075367567}

_SECTION_RE = re.compile(r"^\s*Change in Effective Control\s*$", re.I)
_NURSING_RE = re.compile(r"nursing (home|facility|center|centre)|skilled nursing|\brehab(ilitation)?\b|\bSNF\b", re.I)
_NOT_NH_RE = re.compile(r"home (nursing )?care|home health|visiting (nurse|angels)|hospice|surgery center|\bFASC\b"
                        r"|endoscopy|\bPET\b|imaging|staffing|dialysis|kidney|comfort keepers|ambulatory|dentistry", re.I)
# A single name word that is also a town ("...located in East Providence")
# never identifies a home on its own
_TOWNS = {"PROVIDENCE", "PAWTUCKET", "WARWICK", "CRANSTON", "WOONSOCKET", "COVENTRY", "JOHNSTON", "CUMBERLAND",
          "SMITHFIELD", "BARRINGTON", "PORTSMOUTH", "MIDDLETOWN", "WESTERLY", "BRISTOL", "LINCOLN", "KINGSTON"}
_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_BEDS_RE = re.compile(r"(\d+)[- ]bed\b", re.I)
_SELLER_RE = re.compile(r"(?:licensed and |currently )?operated by (.+?)(?:,? located\b|,|\.|$)", re.I)
# Generic words dropped before matching a CMS home's name in the text
_NAME_STOP = {"THE", "AND", "OF", "INC", "LLC", "CENTER", "CENTRE", "HEALTH", "HEALTHCARE", "CARE", "NURSING",
              "HOME", "REHAB", "REHABILITATION", "OPERATIONS", "DBA", "RI", "NURS", "CTR", "SAINT", "ST", "WEST",
              "SOUTH", "NORTH", "ROYAL", "HARRIS", "CEDAR", "ADVINIACARE", "HOLDING", "COMMUNITY", "RESIDENCE"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper())).strip()


def _date(s: str):
    m = _DATE_RE.search(s or "")
    if not m:
        return None
    mo, d, y = (int(g) for g in m.groups())
    try:
        return datetime(y + 2000 if y < 100 else y, mo, d, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_cec_rows(csv_text: str) -> list[dict]:
    """Rows of the tab's "Change in Effective Control" section."""
    rows, out, inside = list(csv.reader(io.StringIO(csv_text))), [], False
    for r in rows:
        r = [c.strip() for c in r] + [""] * 9
        filled = [c for c in r if c]
        if inside and len(filled) == 1 and r[0] and not r[1]:
            break  # next section heading ("Initial Licensure")
        if _SECTION_RE.match(r[0]) and len(filled) == 1:
            inside = True
            continue
        if not inside or not r[0] or r[0] == "Applicant" or not r[1]:
            continue
        out.append({"applicant": re.sub(r"\s+", " ", r[0]), "project": re.sub(r"\s+", " ", r[1]),
                    "filed": r[2], "review": r[4], "hsc": r[6], "decision": r[7]})
    return out


def match_facility(row: dict, cms_homes: list[tuple[str, str]]) -> str | None:
    """CMS name of the RI home the row is about, by street address or name."""
    text = _norm(f"{row['applicant']} {row['project']} {row['hsc']} {row['decision']}")
    padded = f" {text} "
    for name, address, *_ in cms_homes:
        street = _norm(address).split(" ")[:2]
        if len(street) == 2 and street[0].isdigit() and f" {' '.join(street)} " in padded:
            return name
    # Two distinctive words of the name (or of its d/b/a), then one long one
    for words in (2, 1):
        for name, *_ in cms_homes:
            for part in {name, re.split(r"\bDBA\b", name, flags=re.I)[-1]}:
                core = [w for w in _norm(part).split(" ") if w not in _NAME_STOP and not w.isdigit()]
                if len(core) < words:
                    continue
                phrase = " ".join(core[:words])
                if words == 1 and phrase in _TOWNS:
                    continue
                if len(phrase) >= (6 if words == 2 else 7) and f" {phrase} " in padded:
                    return name
    return None


def is_nursing_home(row: dict, facility: str | None) -> bool:
    text = f"{row['applicant']} {row['project']} {row['hsc']} {row['decision']}"
    if _NOT_NH_RE.search(row["project"]):
        return False
    return bool(facility or _NURSING_RE.search(text))


def decision_party(row: dict) -> str | None:
    """Who the decision letter / council report is addressed to -- usually
    the buyer, also when the applicant is the home's own licensee (Health
    Concepts' six 2024 CECs were filed under each home's company)."""
    for cell in (row["decision"], row["hsc"]):
        text = re.sub(r"^(\s*([A-Z][a-z]+ \d{1,2}(st|nd|rd|th)?,? \d{4}|\d{1,2}/\d{1,2}/\d{2,4})[;,]?)+\s*", "", cell)
        m = re.match(r"(.+?)\s+(?:\d+ (?:\(\w+\) )?CEC\b|CEC\b|State Agency|Report of|Health Services Council|Decision)", text)
        # "...Oak Hill State Agency Decision Letter" names the home, not a buyer
        if m and _date(cell) and _norm(m.group(1)) not in _norm(row["project"]):
            return m.group(1).strip(" ,;")
    return None


def record_url(row: dict, facility: str | None) -> str:
    """Identity of one request, to drop its repeats in later year tabs."""
    first_filed = _date(row["filed"])
    # One applicant can file several (AdviniaCare: five homes on 9/8/2025)
    key = "-".join(filter(None, (_norm(row["applicant"]), _norm(facility or row["project"])[:40]))).replace(" ", "-")
    return f"{CON_RI_SHEET_URL}#cec-{key}" + (f"-{first_filed:%Y%m%d}" if first_filed else "")


def fetch_con_ri_deals(is_known, cms_homes: list[tuple[str, str, str]]) -> list[dict]:
    """cms_homes: (provider_name, provider_address, ccn) of every RI home in CMS."""
    ccn_of = {name: ccn for name, _, ccn in cms_homes}
    rows, seen = [], set()
    for year, gid in CON_RI_TABS.items():
        url = f"https://docs.google.com/spreadsheets/d/{CON_RI_SHEET_ID}/export?format=csv&gid={gid}"
        try:
            resp = cffi_requests.get(url, impersonate="chrome", timeout=60)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"RI CEC sheet {year} fetch failed: {e}")
            health.source_failed("RI CEC applications", f"{year} tab: {e}")
            continue
        tab_rows = parse_cec_rows(resp.text)
        if not tab_rows:
            health.source_failed("RI CEC applications", f"{year} tab has no Change in Effective Control rows (layout change?)")
        for row in tab_rows:
            row["facility"] = match_facility(row, cms_homes)
            url = record_url(row, row["facility"])
            if url not in seen:
                seen.add(url)
                rows.append(row)

    # A buyer's requests filed the same day are one sale of several homes
    # (AdviniaCare: five on 9/8/2025; Health Concepts: five on 8/9/2024);
    # stored one per home they'd also collapse into one in the dedup hash
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        # A withdrawn request never closed (AdviniaCare / Kingston, 2025)
        if not is_nursing_home(row, row["facility"]) or "withdrawn" in f"{row['review']} {row['decision']}".lower():
            continue
        row["buyer"] = decision_party(row) or row["applicant"]
        filed = _date(row["filed"])
        groups.setdefault((_norm(row["buyer"]), filed), []).append(row)

    deals = []
    for (buyer_key, filed), group in groups.items():
        first = group[0]
        buyer = first["buyer"]
        url = f"{CON_RI_SHEET_URL}#cec-{buyer_key.replace(' ', '-')[:60]}" + (f"-{filed:%Y%m%d}" if filed else "")
        if is_known(url):
            continue
        facilities = list(dict.fromkeys(r["facility"] for r in group if r["facility"]))
        decided = max((d for r in group if (d := _date(r["decision"]))), default=None)
        sellers = list(dict.fromkeys(m.group(1).strip() for r in group if (m := _SELLER_RE.search(r["project"]))))
        beds = sum(int(m.group(1)) for r in group if (m := _BEDS_RE.search(r["project"])))
        status = (f"RIDOH decision {decided:%m/%d/%Y}" if decided
                  else first["review"].replace("STATUS:", "").strip() or "pending")
        summary = (
            f"Rhode Island Change in Effective Control application"
            + ("s" if len(group) > 1 else "") + f" by {'; '.join(dict.fromkeys(r['applicant'] for r in group))}"
            + (f" (filed {filed:%m/%d/%Y})" if filed else "")
            + ": " + " / ".join(r["project"].rstrip(".") for r in group) + "."
            + (f" CMS lists the home{'s' if len(facilities) > 1 else ''} as {'; '.join(facilities)}." if facilities else "")
            + (f" The decision is addressed to {buyer}." if buyer != first["applicant"] else "")
            + f" Status: {status}. RIDOH's list gives no price."
        )
        deals.append({
            "url": url,
            "title": f"[RI CEC] {buyer}: {('; '.join(facilities) or first['project'])[:120]}",
            "published_at": filed or datetime.now(timezone.utc),
            "raw_text": f"{summary}\n\nSource: {CON_RI_SHEET_URL} (linked from {CON_RI_INDEX_URL})",
            "source_type": "con",
            "pre_extracted": True,
            "extraction_model": "con_direct",
            "_con_id": url.rsplit("#", 1)[1],
            "acquiring_entity": buyer,
            "seller_entity": "; ".join(sellers) or None,
            "operator_names": [],
            "facility_names": facilities,
            # The sheet names the exact home, so link it by CCN rather than
            # by fuzzy owner-name matching
            **({"ccns": [ccn_of[f] for f in facilities]} if facilities else {}),
            "states": ["RI"],
            "facility_count": max(len(group), len(facilities)),
            "deal_value_m": None,
            "acquisition_date": decided.date().isoformat() if decided else None,
            "financing_amount_m": None,
            "lender": None,
            "rationale": summary + (f" {beds} beds." if beds else ""),
        })
    logger.info(f"RI CEC: {len(rows)} change-in-control requests listed, {len(deals)} new nursing home sales")
    return deals
