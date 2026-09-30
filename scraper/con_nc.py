"""
North Carolina DHSR CON "No Reviews and Exemptions" -- facility acquisitions.

N.C.G.S. 131E-184(a)(8) exempts buying an existing health service facility
from CON review, but the buyer must notify the CON Section first, and every
request is logged in a public HTML table (one per month, current year on
the index page, past years on archive<YEAR>.html): exemption number, county,
facility, facility ID, applicant (the buyer), request date, type, decision
date, description. So this names the buyer and is filed before closing --
Ignite's March 2026 requests led the news by 73 days, the Asheville batch by
95 (docs/con-feasibility.md, "Beyond NCSL's list").

The table covers every facility type. The facility ID doesn't encode the
type, and decision letters are rarely linked, so nursing homes are found by
name within the county:
  - the state's licensed nursing home list (DHSR Nhlist_a.xlsx) has current
    names, which catches facilities renamed *before* the filing;
  - CMS's facility list lags renames, so it still has the old name for ones
    renamed *after* (Sardis Oaks -> Ignite Medical Resort);
  - failing both, a nursing-words test on the name.
Parsed without Claude.
"""

import html
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone

import openpyxl
from curl_cffi import requests as cffi_requests
from rapidfuzz import fuzz

from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_NC_SOURCE_NAME = "North Carolina DHSR CON Exemptions"
CON_NC_INDEX_URL = "https://info.ncdhhs.gov/dhsr/coneed/reviews/index.html"
CON_NC_ARCHIVE_URL = "https://info.ncdhhs.gov/dhsr/coneed/reviews/archive{year}.html"
NC_NURSING_HOME_LIST_URL = "https://info.ncdhhs.gov/dhsr/data/Nhlist_a.xlsx"
CON_NC_RECENCY_DAYS = 365
NAME_MATCH_MIN = 85
# Two filings for one facility this close together are one deal re-filed
# under a different buyer entity (Huntersville Oaks: 3/24 and 5/20/2026)
REFILING_WINDOW_DAYS = 180

_OWNERSHIP_RE = re.compile(
    r"acqui|purchase|change (?:in|of) (?:the )?(?:indirect |direct )?(?:ownership|operator|owner|licensee)"
    r"|indirect ownership|ownership interest|\bCHOW\b|lease (?:of )?(?:the )?facility", re.I)
_EQUIPMENT_RE = re.compile(r"scanner|\bMRI\b|\bCT\b|PET|robot|da ?vinci|equipment|mammograph|linear accel|cath", re.I)
_NURSING_WORDS_RE = re.compile(r"nursing|rehab|health ?care center|post[- ]acute|long[- ]term care|skilled|convalescent", re.I)
_NOT_NURSING_RE = re.compile(
    r"assisted living|senior living|memory care|home health|hospice|hospital|surgery|surgical|endoscopy|imaging"
    r"|rest home|group home|residential|retirement|adult care|family care|medical (?:center|clinic|group)", re.I)
_NAME_NOISE_RE = re.compile(r"\b(?:llc|inc|the|of|and|center|centre|a)\b|[^a-z0-9 ]")


def _get(url: str, timeout: int = 60):
    resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", _NAME_NOISE_RE.sub(" ", name.lower().replace("&", " "))).strip()


def _parse_rows(page_html: str) -> list[dict]:
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)
        c = [_clean(x) for x in cells]
        if len(c) < 10 or not c[0].isdigit() or not c[2]:
            continue
        try:
            requested = datetime.strptime(c[5], "%m/%d/%Y").date()
        except ValueError:
            continue
        letter = re.search(r'href="([^"]+\.pdf)"', tr)
        rows.append({
            "exemption_no": c[0], "county": c[1], "facility": c[2], "facility_id": c[3],
            # Some applicants carry an address: "Durham Operator LLC. -411 South LaSalle ..."
            "applicant": re.split(r"\s+[-\u2013]\s*\d", c[4])[0].strip(" .,"), "requested": requested, "type": c[6], "decided": c[8],
            "description": c[9],
            "letter": ("https://info.ncdhhs.gov" + letter.group(1)).replace(" ", "%20") if letter else None,
        })
    return rows


def load_licensed_nursing_homes() -> list[tuple[str, str]]:
    """(county lower-case, facility name) for every NC-licensed nursing home."""
    ws = openpyxl.load_workbook(io.BytesIO(_get(NC_NURSING_HOME_LIST_URL).content), read_only=True).worksheets[0]
    return [(str(r[0]).strip().lower(), str(r[4]).strip())
            for r in ws.iter_rows(values_only=True)
            if r and r[1] and str(r[1]).startswith("NH") and r[0] and r[4]]


def is_nursing_home(row: dict, licensed: list[tuple[str, str]], cms_names: list[str]) -> bool:
    name = _norm(row["facility"])
    county = row["county"].lower()
    if any(fuzz.token_set_ratio(name, _norm(n)) >= NAME_MATCH_MIN for c, n in licensed if c == county):
        return True
    if any(fuzz.token_sort_ratio(name, _norm(n)) >= 90 for n in cms_names):
        return True
    return bool(_NURSING_WORDS_RE.search(row["facility"])) and not _NOT_NURSING_RE.search(row["facility"])


def record_url(exemption_no: str) -> str:
    return f"{CON_NC_INDEX_URL}#exemption-{exemption_no}"


def fetch_con_nc_deals(is_known, cms_names: list[str]) -> list[dict]:
    """
    Pre-extracted deal dicts for nursing home acquisition exemptions not yet
    stored. cms_names: CMS provider names for NC (upper or mixed case).
    """
    today = date.today()
    try:
        rows = _parse_rows(_get(CON_NC_INDEX_URL).text)
    except Exception as e:
        logger.error(f"NC CON index fetch failed: {e}")
        health.source_failed("NC CON exemptions", f"index fetch failed: {e}")
        return []
    if not rows:
        health.source_failed("NC CON exemptions", "index parsed to 0 rows (layout change?)")
        return []
    try:
        rows += _parse_rows(_get(CON_NC_ARCHIVE_URL.format(year=today.year - 1)).text)
    except Exception as e:
        logger.warning(f"NC CON {today.year - 1} archive fetch failed: {e}")
    try:
        licensed = load_licensed_nursing_homes()
    except Exception as e:
        # Still usable via CMS names + the nursing-words test
        logger.warning(f"NC licensed nursing home list fetch failed: {e}")
        health.failed("NC CON exemptions", f"licensed NH list: {e}")
        licensed = []

    cutoff = today - timedelta(days=CON_NC_RECENCY_DAYS)
    candidates = sorted(
        (r for r in rows
         if r["requested"] >= cutoff and _OWNERSHIP_RE.search(r["description"])
         and not _EQUIPMENT_RE.search(r["description"]) and is_nursing_home(r, licensed, cms_names)),
        key=lambda r: r["requested"])

    deals, last_filed = [], {}
    for row in candidates:
        key = (_norm(row["facility"]), row["county"].lower())
        prior = last_filed.get(key)
        if prior and (row["requested"] - prior).days <= REFILING_WINDOW_DAYS:
            continue  # re-filing of a deal already taken from this batch
        last_filed[key] = row["requested"]
        if is_known(record_url(row["exemption_no"])):
            continue

        summary = (
            f"North Carolina CON exemption {row['exemption_no']} ({row['type']}): "
            f"{row['facility']}, {row['county']} County (facility ID {row['facility_id']}). "
            f"Applicant: {row['applicant']}. Requested {row['requested']:%m/%d/%Y}"
            + (f", decided {row['decided']}" if row["decided"] else "")
            + f". Project: {row['description']}."
        )
        deals.append({
            "url": record_url(row["exemption_no"]),
            "title": f"[NC CON] {row['exemption_no']} {row['description']}: {row['facility']}",
            "published_at": datetime.combine(row["requested"], datetime.min.time()).replace(tzinfo=timezone.utc),
            "raw_text": f"{summary}\n\nSource: {row['letter'] or CON_NC_INDEX_URL}",
            "source_type": "con",
            "pre_extracted": True,
            "extraction_model": "con_direct",
            "_con_id": row["exemption_no"],
            "acquiring_entity": row["applicant"] or None,
            "seller_entity": None,
            "operator_names": [row["applicant"]] if row["applicant"] else [],
            "facility_names": [row["facility"]],
            "states": ["NC"],
            "facility_count": 1,
            "deal_value_m": None,
            "acquisition_date": row["requested"].isoformat(),
            "financing_amount_m": None,
            "lender": None,
            "rationale": summary,
        })

    logger.info(f"NC CON: {len(rows)} exemptions listed, {len(deals)} new nursing home acquisitions")
    return deals
