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
  FL  FloridaHealthFinder (AHCA) facility locator, nursing homes, its own
      "Export Facility Data - CSV" button: Owner, Owner Since (the date
      the current owner took over -- a sale date), Management Company. No
      CCN, so homes are matched to CMS by street address, then by name in
      the same city. The export posts the licence and link IDs the search
      returned (embedded in the results page); without the link IDs it
      redirects to the home page. No robots.txt.
  NY  health.data.ny.gov "Health Facility General Information" (DOH HFIS,
      vn5v-hh5r), ~weekly: Operator Name per nursing home. No CCN (matched
      like FL) and no change date (Facility Open Date is the home's
      original opening, 1901 for many), so like CA its first run only
      stores the baseline. NY's pre-closing PHHPC exhibits are con_ny.py.

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
    "FL": {
        "source_name": "Florida AHCA FloridaHealthFinder Nursing Home List",
        "page_url": "https://quality.healthfinder.fl.gov/Facility-Search/FacilityLocateSearch",
        "csv_url": "https://quality.healthfinder.fl.gov/Facility-Provider/Nursing-Home?handler=ExportAsCSV",
        "label": "Florida's facility list (AHCA, FloridaHealthFinder)",
    },
    "NY": {
        "source_name": "New York DOH Health Facility List (HFIS)",
        "page_url": "https://health.data.ny.gov/Health/Health-Facility-General-Information/vn5v-hh5r",
        "csv_url": "https://health.data.ny.gov/api/views/vn5v-hh5r/rows.csv?accessType=DOWNLOAD",
        "label": "New York's facility list (DOH HFIS, health.data.ny.gov)",
    },
}
# Lists without a CCN column, matched to CMS by address and name
NO_CCN_STATES = ("FL", "NY")
FL_BASE = "https://quality.healthfinder.fl.gov"

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
        elif state == "FL":
            if r.get("Facility Type") != "Nursing Home" or r.get("Closed Date"):
                continue
            rows.append({"ccn": None, "licensee": (r.get("Owner") or "").strip(),
                         "licence_date": _date((r.get("Owner Since") or "").split(" ")[0]),
                         "facility": r["Name"].strip(), "city": r["Street City"].strip(),
                         "address": r["Street Address"].strip(),
                         "manager": (r.get("Management Company") or "").strip() or None})
        elif state == "NY":
            if r.get("Short Description") != "NH":
                continue
            rows.append({"ccn": None, "licensee": r["Operator Name"].strip(), "licence_date": None,
                         "facility": r["Facility Name"].strip(), "city": r["Facility City"].strip(),
                         "address": r["Facility Address 1"].strip()})
    # HFIS repeats some homes (one row per certificate); keep one per home
    unique = {(r["facility"].upper(), r.get("address", "").upper(), r["licensee"].upper()): r for r in rows}
    return [r for r in unique.values() if r["licensee"]]


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
        + (f"; managed by {row['manager']}" if row.get("manager") else "")
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


def _fetch_fl_csv() -> str:
    """The locator's own export: search nursing homes, then post the licence
    IDs on the results page to the CSV handler, as its button does."""
    def token(page):
        return re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page).group(1)
    s = cffi_requests.Session(impersonate="chrome")
    page = s.get(LISTS["FL"]["page_url"], timeout=60).text
    page = s.post(f"{FL_BASE}/Facility-Search/FacilityLocateSearch?handler=AdvancedSearch", timeout=120,
                  data={"FacilityTypeSelection": "Nursing-Home", "__RequestVerificationToken": token(page)}).text
    ids = re.findall(r'"LicenseID":"(\d+)","LicenseNumber":"[^"]*","LicenseStatus":"[^"]*","LinkId":"(\d+)"', page)
    form = page[page.find('id="exportForm"'):]
    resp = s.post(LISTS["FL"]["csv_url"], timeout=180, allow_redirects=False,
                  data={"LicenseID": ",".join(i for i, _ in ids), "LinkID": ",".join(l for _, l in ids),
                        "__RequestVerificationToken": token(form)})
    resp.raise_for_status()
    if not resp.text.startswith("Name,"):
        raise RuntimeError(f"export returned no CSV (HTTP {resp.status_code}, {len(ids)} licence IDs)")
    return resp.text


_CITY_SUBS = [(r"\bST\b", "SAINT"), (r"\bFT\b", "FORT"), (r"\bBCH\b", "BEACH"), (r"\bN\b", "NORTH"),
              (r"\bPT\b", "PORT"), (r"\bSPGS\b", "SPRINGS")]


def _city(city: str) -> str:
    c = re.sub(r"[^A-Z ]", " ", (city or "").upper())
    for a, b in _CITY_SUBS:
        c = re.sub(a, b, c)
    return " ".join(c.split())


def _street_key(address: str) -> str:
    """'4600 54TH AVE S' -> '4600 54TH': number plus first street word
    ('490 SOUTH OLD WIRE RD' is CMS's '490 S OLD WIRE RD')."""
    words = [re.sub(r"^(\d+)(?:ST|ND|RD|TH)$", r"\1", w)  # "79 St" is "79TH STREET"
             for w in re.sub(r"[^A-Z0-9 ]", " ", (address or "").upper()).split()
             if w not in ("N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST")]
    return " ".join(words[:2]) if words and words[0].isdigit() else ""


def match_ccns(rows: list[dict], cms_homes: list[tuple[str, str, str, str]]) -> None:
    """Fill each FL/NY row's CCN from CMS (name, address, city, ccn): the one
    home at the same street number and street in the same city, else the
    one home in the city whose name agrees."""
    for row in rows:
        city, street = _city(row["city"]), _street_key(row["address"])
        same_city = [h for h in cms_homes if _city(h[2]) == city]
        hits = {h[3] for h in same_city if street and _street_key(h[1]) == street}
        if len(hits) != 1:
            hits = {h[3] for h in same_city if fuzz.token_sort_ratio(_norm(row["facility"]), _norm(h[0])) >= 90}
        if len(hits) != 1 and street:  # AHCA and CMS name the town differently (Cutler Ridge / Miami)
            hits = {h[3] for h in cms_homes if _street_key(h[1]) == street}
        row["ccn"] = hits.pop() if len(hits) == 1 else None
    taken = {}
    for row in rows:
        if row["ccn"]:
            taken.setdefault(row["ccn"], []).append(row)
    for same in taken.values():  # two AHCA licences on one CMS home: can't tell which
        if len(same) > 1:
            for row in same:
                row["ccn"] = None


def fetch_licence_list(state: str) -> list[dict] | None:
    cfg = LISTS[state]
    try:
        if state == "FL":
            text = _fetch_fl_csv()
        else:
            resp = cffi_requests.get(cfg["csv_url"], impersonate="chrome", timeout=180)
            resp.raise_for_status()
            text = resp.text
    except Exception as e:
        logger.error(f"{state} licence list fetch failed: {e}")
        health.source_failed(f"{state} licence list", f"{cfg['csv_url']}: {e}")
        return None
    rows = parse_rows(state, text)
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
        if state in NO_CCN_STATES:
            cur.execute("""SELECT provider_name, provider_address, provider_city, ccn
                           FROM cms_facilities WHERE provider_state = %s""", (state,))
            match_ccns(rows, cur.fetchall())
            unmatched = sum(1 for r in rows if not r["ccn"])
            if unmatched:
                logger.info(f"{state} licence list: {unmatched} homes not matched to a CMS home")
            rows = [r for r in rows if r["ccn"]]
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
