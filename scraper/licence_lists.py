"""
State nursing home licence lists -- a new licensee on a CMS-listed home.

Where a state publishes no sale filing, its open licence list still names
each home's licensee, and a sale (or operator change) issues a licence to
the new operator. Each run compares the list with the licensee stored for
the home at the last check (licence_list_snapshot); the first run for a
state compares with CMS's legal names instead, limited to licences dated in
the last year, so it doesn't report decades-old differences (CA's list has
no such date, so its first run only stores the baseline).

  MO  data.mo.gov "LTC Directory" (fenu-sipv), DHSS licence list, ~monthly:
      Entity Name, licence effective date, CCN ("Certification").
      2026-10-05 copy: ~25 homes to "... SNF OPCO LLC" licensees on 6/1/2026.
  CA  data.chhs.ca.gov "Licensed and Certified Healthcare Facility
      Locations" (CDPH), ~monthly: BUSINESS_NAME, CCN. No usable change
      date: INITIAL_LICENSE_DATE stays the home's first licence after a
      sale (West Shore: 1995) and the effective date renews every year.
      robots.txt disallows /api/, so only the plain /download URL is used.

The new licensee is usually an operating company ("Milan SNF Opco LLC"),
not the parent, so these are facility-level records: buyer = licensee,
seller = the previous licensee. A licensee that CMS's CHOW file already
names as the home's buyer is skipped (CHOW covers it). A changed home's
snapshot row is only moved on once its record is stored (seen on a later
run), so a failed run can't lose it. See docs/con-feasibility.md.
"""

import csv
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone

from curl_cffi import requests as cffi_requests
from rapidfuzz import fuzz

from pipeline.run_health import health

logger = logging.getLogger(__name__)

SEED_DAYS = 365

LISTS = {
    "MO": {
        "source_name": "Missouri DHSS Long-Term Care Licence List",
        "page_url": "https://data.mo.gov/Health/LTC-Directory/fenu-sipv",
        "csv_url": "https://data.mo.gov/api/views/fenu-sipv/rows.csv?accessType=DOWNLOAD",
        "label": "Missouri's licence list (DHSS, data.mo.gov)",
    },
    "CA": {
        "source_name": "California CDPH Licensed Facility List",
        "page_url": "https://data.chhs.ca.gov/dataset/licensed-and-certified-healthcare-facility-listing",
        "csv_url": ("https://data.chhs.ca.gov/dataset/3b5b80e8-6b8d-4715-b3c0-2699af6e72e5/resource/"
                    "f0ae5731-fef8-417f-839d-54a0ed3a126e/download"),
        "label": "California's licence list (CDPH, data.chhs.ca.gov)",
    },
}

_ENTITY_NOISE_RE = re.compile(
    r"\b(THE|LLC|L L C|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LP|L P|LTD|LIMITED|LIABILITY|PLLC|DBA|NO)\b")
# Some CMS "legal names" are the home's street address ("4401 NORTH HANLEY ROAD")
_ADDRESS_RE = re.compile(r"^\d+\s")


def _norm(name: str) -> str:
    s = (name or "").upper().replace("#", " NO ").replace("&", " AND ").replace("’", "'")
    s = re.sub(r"[^A-Z0-9 ]", " ", s.replace("'", ""))
    return " ".join(_ENTITY_NOISE_RE.sub(" ", s).split())


def same_entity(a: str, b: str) -> bool:
    """Spelling and truncation differences are not a new licensee: MO cuts
    names at 40 characters, "WAVERLY #1, INC" is CMS's "WAVERLY 1 INC"."""
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    if x.replace(" ", "") == y.replace(" ", "") or (
            min(len(x), len(y)) >= 15 and (x.startswith(y) or y.startswith(x))):
        return True
    return fuzz.token_sort_ratio(x, y) >= 92


def _date(s: str):
    s = (s or "").strip()[:10]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_rows(state: str, csv_text: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(csv_text)):
        if state == "MO":
            if r.get("Level of Care") != "SNF" or not (r.get("Certification") or "").strip().isdigit():
                continue
            rows.append({"ccn": r["Certification"].strip().zfill(6), "licensee": r["Entity Name"].strip(),
                         "licence_date": _date(r["LicenseEffDate"]), "facility": r["Facility Name"].strip(),
                         "city": r["City"].strip()})
        elif state == "CA":
            if (r.get("FAC_TYPE_CODE") != "SNF" or not (r.get("CCN") or "").strip()
                    or (r.get("LICENSE_STATUS_DESCRIPTION") or "").upper() != "ACTIVE"):
                continue
            rows.append({"ccn": r["CCN"].strip().zfill(6), "licensee": r["BUSINESS_NAME"].strip(),
                         "licence_date": None, "facility": r["FACNAME"].strip(),
                         "city": r["CITY"].strip()})
    return [r for r in rows if r["licensee"]]


def record_url(state: str, row: dict) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", _norm(row["licensee"]).lower()).strip("-")[:60]
    return f"{LISTS[state]['page_url']}#licensee-{row['ccn']}-{key}"


def detect_changes(state: str, rows: list[dict], previous: dict, cms_names: dict, chow_buyers: dict,
                   is_known, today: date | None = None) -> tuple[list[dict], list[dict]]:
    """-> (changed rows, each with "seller"; snapshot rows to write).

    previous: ccn -> licensee at the last check ({} on a state's first run)
    cms_names: ccn -> CMS legal names (current and past) of CMS-listed homes
    chow_buyers: ccn -> buyer names in CMS's CHOW file
    """
    today = today or date.today()
    seed_since = today - timedelta(days=SEED_DAYS)
    changes, snapshot = [], []
    for row in rows:
        if row["ccn"] not in cms_names:
            continue
        prev = previous.get(row["ccn"])
        if prev is not None:
            changed, seller = not same_entity(row["licensee"], prev), prev
        else:
            names = [n for n in cms_names[row["ccn"]] if not _ADDRESS_RE.match(n)]
            changed = (bool(names) and bool(row["licence_date"]) and row["licence_date"] >= seed_since
                       and not any(same_entity(row["licensee"], n) for n in names))
            seller = names[0] if names else None
        if changed and any(same_entity(row["licensee"], b) for b in chow_buyers.get(row["ccn"], ())):
            changed = False  # CMS's CHOW file already has this sale
        if changed and not is_known(record_url(state, row)):
            changes.append({**row, "seller": seller})
            continue  # snapshot moves on once the record is stored
        snapshot.append(row)
    return changes, snapshot


def to_deal(state: str, row: dict) -> dict:
    cfg = LISTS[state]
    when = f" from {row['licence_date']:%m/%d/%Y}" if row["licence_date"] else ""
    summary = (
        f"{cfg['label']} shows {row['facility']} ({row['city'].title()}) licensed to {row['licensee']}{when}"
        + (f"; the previous licensee was {row['seller']}" if row.get("seller") else "")
        + ". A new licensee usually means a sale or a new operator; the list names the operating "
          "company, not its parent, and gives no price."
    )
    published = (datetime.combine(row["licence_date"], datetime.min.time()).replace(tzinfo=timezone.utc)
                 if row["licence_date"] else datetime.now(timezone.utc))
    url = record_url(state, row)
    return {
        "url": url,
        "title": f"[{state} licence] {row['facility']}: new licensee {row['licensee']}",
        "published_at": published,
        "raw_text": f"{summary}\n\nSource: {cfg['page_url']}",
        "source_type": "con",
        "pre_extracted": True,
        "extraction_model": "con_direct",
        "_con_id": url.rsplit("#", 1)[1],
        "acquiring_entity": row["licensee"],
        "seller_entity": row.get("seller"),
        "operator_names": [row["licensee"]],
        "facility_names": [row["facility"]],
        "ccns": [row["ccn"]],
        "states": [state],
        "facility_count": 1,
        "deal_value_m": None,
        "acquisition_date": row["licence_date"].isoformat() if row["licence_date"] else None,
        "financing_amount_m": None,
        "lender": None,
        "rationale": summary,
    }


def fetch_licence_list(state: str) -> list[dict] | None:
    cfg = LISTS[state]
    try:
        resp = cffi_requests.get(cfg["csv_url"], impersonate="chrome", timeout=180)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"{state} licence list fetch failed: {e}")
        health.source_failed(f"{state} licence list", f"{cfg['csv_url']}: {e}")
        return None
    rows = parse_rows(state, resp.text)
    if len(rows) < 100:
        health.source_failed(f"{state} licence list", f"only {len(rows)} nursing home rows (layout change?)")
        return None
    return rows


def licence_list_deals(state: str, conn, is_known) -> list[dict]:
    """Diff one state's list against the stored snapshot; write the snapshot
    rows that are settled; return the new-licensee records."""
    rows = fetch_licence_list(state)
    if rows is None:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT ccn, licensee FROM licence_list_snapshot WHERE state = %s", (state,))
        previous = dict(cur.fetchall())
        # current legal name first: it's the seller on a first run
        cur.execute("""SELECT ccn, legal_business_name, 0 FROM cms_facilities WHERE provider_state = %s
                       UNION ALL SELECT f.ccn, n.legal_business_name, 1 FROM cms_facilities f
                       JOIN cms_legal_names n ON n.ccn = f.ccn WHERE f.provider_state = %s
                       ORDER BY 3""", (state, state))
        cms_names: dict[str, list[str]] = {}
        for ccn, name, _ in cur.fetchall():
            cms_names.setdefault(ccn, [])
            if name and name not in cms_names[ccn]:
                cms_names[ccn].append(name)
        cur.execute("SELECT ccn, buyer_name FROM chow_seen_records WHERE ccn = ANY(%s)", (list(cms_names),))
        chow_buyers: dict[str, list[str]] = {}
        for ccn, buyer in cur.fetchall():
            chow_buyers.setdefault(ccn, []).append(buyer)
    changes, snapshot = detect_changes(state, rows, previous, cms_names, chow_buyers, is_known)
    with conn.cursor() as cur:
        for row in snapshot:
            cur.execute("""INSERT INTO licence_list_snapshot (state, ccn, licensee, licence_date, seen_at)
                           VALUES (%s, %s, %s, %s, now())
                           ON CONFLICT (state, ccn) DO UPDATE
                           SET licensee = EXCLUDED.licensee, licence_date = EXCLUDED.licence_date, seen_at = now()""",
                        (state, row["ccn"], row["licensee"], row["licence_date"]))
    conn.commit()
    logger.info(f"{state} licence list: {len(rows)} nursing homes, "
                f"{'first run, ' if not previous else ''}{len(changes)} new licensees")
    return [to_deal(state, row) for row in changes]
