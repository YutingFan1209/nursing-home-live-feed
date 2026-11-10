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
  TX  HHSC TULIP LTC Provider Search (tx_tulip.py): each nursing facility's
      "Owner" (licence holder), read page by page in headless Chromium,
      about 45 minutes, so only when the last read is over 25 days old.
      No CCN (matched like NY) and no date, so the first read is the
      baseline. Many owners are hospital districts with a private operator,
      whose sale doesn't show here.
  KY  OIG "Long Term Care Directory" XLSX, updated the 1st of each month:
      OWNER per licence (nursing facility and nursing home beds; hospital
      swing units drop out at CMS matching). No CCN (matched by address)
      and the licensure effective date renews yearly, so the first read is
      the baseline.
  KS  KDADS "Adult Care Home Directory", nursing facilities (an Oracle APEX
      report; its CSV export). Terms of use: K.S.A. 45-230, no selling to or
      soliciting the people listed. "Facility Name" is the licensed company
      when a DBA is given (agrees with CMS's legal name for 136 of 147);
      without a DBA it is the home's own name, so those rows are left out.
      "Last Change Date" with "Last Change" LESSEE, ADMINISTRATOR/OPERATOR,
      BUILDING OWNER or MANAGEMENT FIRM is the change date. No CCN (matched
      by address).
  WI  DHS "Nursing Home Directory" XLSX (a date row, then OWNER, LIC#,
      PROVIDER, ..., CERTIFICATION = CCN). Owner carries a "(FOR PROFIT
      CORPORATION)" style suffix, dropped. No change date: baseline first.
  NE  DHHS Licensure Unit "Long Term Care Facilities" roster PDF (nursing
      homes by town, "Updated" about monthly): Licensee and Medicare ID
      (CCN) per home. Read from the PDF's text. Homes without a Medicare
      ID (veterans' homes, state facilities) are left out. No change date:
      baseline first.
  WA  DSHS Nursing Home Locator ("Show All Facilities", fortress.wa.gov):
      no licensee column, but each home's state licence number. A WA
      licence isn't transferable (RCW 18.51.050), so a sale issues a new
      number (they run in order: 1641 in 2021, 1696-1706 Aug 2024,
      1724-1747 mid-2025). The diff key is the number; the buyer comes
      from the home's newest DSHS enforcement letter that shows the new
      number ("Licensee Information: ..."), when one is posted yet. No
      CCN (matched by address), no date: baseline first.
  IA  DIAL Health Facility Database (dia-hfd.iowa.gov), the public entity
      search's own JSON (/Home/EntitySearchAjax, free-standing NF/SNF types):
      CCN, entity name and Current License Date per home. The name is mostly
      the DBA (8 of 24 recent CHOW buyers match it), so the diff key is the
      date: licences run 365 days from the date issued, and a sale issues a
      new licence on the sale date (70 of 79 homes sold since 2023 carry the
      CHOW's month and day). A date off the old anniversary = a new licence.
      The buyer is the new entity name when it's a company name; the home's
      Related Entities tab (its owner group's homes) is added for context.
      licenseNumber in the JSON is the administrator's; the state licence is
      classificationNumber. No robots.txt. Baseline first.
  MI  LARA statewide licensing search (statelicensing.apps.lara.state.mi.us):
      licensee and the licence's original effective date, but only on each
      home's detail record. Read in the person's own Chrome
      (scripts/mi_licence_browser.js, ~15 min), then
      scripts/licence_browser.py ingest. No CCN (matched like NY).
  OH  ODH EID "FACILITY LISTING" report with owners, its own CSV export,
      read in the person's own Chrome (scripts/oh_licence_browser.js).
      Owner company per home, no CCN, no change date (OPENDATE is the
      home's opening), so the first read is the baseline.
  MI and OH are browser-only (BROWSER_STATES): main.py skips them; they run
  every two weeks by hand (2026-11-09, user).

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
    "NC": {
        "source_name": "North Carolina DHSR Nursing Home Licence List",
        "page_url": "https://info.ncdhhs.gov/dhsr/reports.htm",
        "csv_url": "https://info.ncdhhs.gov/dhsr/data/Nhlist_a.xlsx",
        "label": "North Carolina's licence list (DHSR)",
    },
    "KY": {
        "source_name": "Kentucky OIG Long-Term Care Directory",
        "page_url": "https://chfs.ky.gov/agencies/os/oig/dhc/Pages/hcf.aspx",
        "csv_url": "https://chfs.ky.gov/agencies/os/oig/dhc/Documents/Long%20Term%20Care%20Directory.xlsx",
        "label": "Kentucky's long-term care directory (OIG)",
    },
    "KS": {
        "source_name": "Kansas KDADS Adult Care Home Directory",
        "page_url": "https://kdads.ks.gov/licensing-policy/adult-care-home-directory",
        # Oracle APEX report (nursing facilities); its own CSV export
        "csv_url": "https://webapps.kdads.ks.gov/prod/f?p=113:901:::::P901_FACILITY_TYPE,P901_SOURCE:NF,P",
        "label": "Kansas's adult care home directory (KDADS)",
    },
    "WI": {
        "source_name": "Wisconsin DHS Nursing Home Directory",
        "page_url": "https://www.dhs.wisconsin.gov/guide/nursing-home.htm",
        "csv_url": "https://www.dhs.wisconsin.gov/guide/nhdirexcel.xlsx",
        "label": "Wisconsin's nursing home directory (DHS)",
    },
    "NE": {
        "source_name": "Nebraska DHHS Nursing Home Roster",
        "page_url": "https://dhhs.ne.gov/licensure/Pages/Rosters-of-Facilities-and-Services.aspx",
        "csv_url": "https://dhhs.ne.gov/licensure/Documents/LTCRoster.pdf",
        "label": "Nebraska's nursing home roster (DHHS)",
    },
    "MI": {
        "source_name": "Michigan LARA Nursing Home Licence Search",
        "page_url": "https://statelicensing.apps.lara.state.mi.us/",
        "csv_url": "https://statelicensing.apps.lara.state.mi.us/",
        "label": "Michigan's licence search (LARA)",
    },
    "OH": {
        "source_name": "Ohio ODH Nursing Home Facility Listing (EID)",
        "page_url": "https://publicapps.odh.ohio.gov/eid/reports/EID_Report_Criteria.aspx",
        "csv_url": "https://publicapps.odh.ohio.gov/eid/reports/EID_Report_Criteria.aspx",
        "label": "Ohio's facility listing with owners (ODH)",
    },
    "WA": {
        "source_name": "Washington DSHS Nursing Home Locator",
        "page_url": "https://fortress.wa.gov/dshs/adsaapps/lookup/NHPubLookup.aspx",
        "csv_url": "https://fortress.wa.gov/dshs/adsaapps/lookup/NHPubLookup.aspx",
        "label": "Washington's nursing home locator (DSHS)",
    },
    "IA": {
        "source_name": "Iowa DIAL Health Facility Database",
        "page_url": "https://dia-hfd.iowa.gov/",
        "csv_url": "https://dia-hfd.iowa.gov/Home/EntitySearchAjax",
        "label": "Iowa's health facility database (DIAL)",
    },
    "TX": {
        "source_name": "Texas HHSC TULIP LTC Provider Search",
        "page_url": "https://tulip.hhs.texas.gov/TULIP/s/ltc-provider-search",
        "csv_url": "https://tulip.hhs.texas.gov/TULIP/s/ltc-provider-search",
        "label": "Texas's provider search (HHSC TULIP)",
    },
}
# TX reads ~1,281 pages; reread only when the last read is this old
TX_REREAD_DAYS = 25
# Lists without a CCN column, matched to CMS by address and name
NO_CCN_STATES = ("FL", "NY", "TX", "NC", "KY", "KS", "MI", "OH", "WA")
# Read in the person's own Chrome (scripts/licence_browser.py), not by main.py
BROWSER_STATES = ("MI", "OH")
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


def parse_nc_xlsx(content: bytes) -> list[dict]:
    """DHSR's monthly "Nursing Home Listing Alphabetical": a title block, then
    a header row with "Name of Licensee Legal Name". No CCN, no date."""
    from openpyxl import load_workbook
    sheet = load_workbook(io.BytesIO(content), read_only=True).active
    header, rows = None, []
    for values in sheet.iter_rows(values_only=True):
        cells = [str(v).strip() if v is not None else "" for v in values]
        if header is None:
            if "Name of Licensee Legal Name" in cells:
                header = cells
            continue
        r = dict(zip(header, cells))
        if not r.get("License #", "").startswith("NH") or not r.get("Name of Licensee Legal Name"):
            continue
        rows.append({"ccn": None, "licensee": r["Name of Licensee Legal Name"], "licence_date": None,
                     "facility": r["DBA Name"], "city": r.get("Site City") or r.get("Facility City", ""),
                     "address": r.get("Site Address") or r.get("Facility Address", "")})
    return rows


# KDADS "Last Change" kinds that can mean a new licensee or owner
_KS_OWNER_CHANGES = {"LESSEE", "ADMINISTRATOR/OPERATOR", "BUILDING OWNER", "MANAGEMENT FIRM"}


def parse_ks_csv(text: str) -> list[dict]:
    """KDADS directory CSV: Facility Name, DBA, State ID, ..., Last Change
    Date, Last Change. Only rows with a DBA name the licensed company."""
    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("\ufeff"))):
        if not r["DBA"].strip() or not r["Facility Name"].strip():
            continue
        when = None
        if r["Last Change"].strip() in _KS_OWNER_CHANGES and r["Last Change Date"].strip():
            when = datetime.strptime(r["Last Change Date"].strip(), "%m/%d/%Y").date()
        rows.append({"ccn": None, "licensee": " ".join(r["Facility Name"].split()), "licence_date": when,
                     "facility": " ".join(r["DBA"].split()).title(), "city": r["City"].strip(),
                     "address": r["Address"].strip()})
    return rows


def fetch_ks_csv() -> str:
    """Open the report (APEX gives a session id), then ask for its CSV."""
    session = cffi_requests.Session(impersonate="chrome")
    page = session.get(LISTS["KS"]["csv_url"], timeout=120)
    page.raise_for_status()
    sid = re.search(r'name="p_instance" value="(\d+)"', page.text).group(1)
    resp = session.get(f"https://webapps.kdads.ks.gov/prod/f?p=113:901:{sid}:CSV:NO:::", timeout=120)
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig")
    if not text.startswith("Facility Name,DBA,"):
        raise ValueError("KDADS export is not the directory CSV")
    return text


def parse_ky_xlsx(content: bytes) -> list[dict]:
    """OIG's monthly directory: one sheet, header row first."""
    from openpyxl import load_workbook
    sheet = load_workbook(io.BytesIO(content), read_only=True).worksheets[0]
    it = sheet.iter_rows(values_only=True)
    header = [str(v or "").strip() for v in next(it)]
    rows = []
    for values in it:
        r = dict(zip(header, values))
        if not (r.get("NF") or r.get("NH")) or not str(r.get("OWNER") or "").strip():
            continue
        rows.append({"ccn": None, "licensee": " ".join(str(r["OWNER"]).split()), "licence_date": None,
                     "facility": " ".join(str(r["Facility Name"]).split()), "city": str(r["City"] or "").strip(),
                     "address": str(r["Address"] or "").strip()})
    return rows


# "BEDROCK HCS AT ABBOTSFORD LLC (FOR PROFIT CORPORATION)": the kind of owner
_WI_KIND_RE = re.compile(r"\s*\([A-Z /,-]*\)\s*$")


def parse_wi_xlsx(content: bytes) -> list[dict]:
    """DHS's nursing home directory: a date row, then the header."""
    from openpyxl import load_workbook
    sheet = load_workbook(io.BytesIO(content), read_only=True).worksheets[0]
    header, rows = None, []
    for values in sheet.iter_rows(values_only=True):
        cells = [str(v).strip() if v is not None else "" for v in values]
        if header is None:
            if "OWNER" in cells and "CERTIFICATION" in cells:
                header = cells
            continue
        r = dict(zip(header, cells))
        if not r["CERTIFICATION"].isdigit() or not r["OWNER"]:
            continue
        rows.append({"ccn": r["CERTIFICATION"].zfill(6),
                     "licensee": _WI_KIND_RE.sub("", " ".join(r["OWNER"].split())),
                     "licence_date": None, "facility": r["PROVIDER"], "city": r["CITY"], "address": r["STREET"]})
    return rows


# "AINSWORTH ( BROWN )  - " then the ZIP: the start of each home in NE's roster
_NE_HOME_RE = re.compile(r"^([A-Z][A-Z .'-]+?) \( ?[A-Z .'-]+ ?\)\s+-\s*\n(\d{5})\n", re.M)


def parse_ne_pdf(content: bytes) -> list[dict]:
    """Each home's text runs: name, street (split over lines), phone and
    fax ("988-7115"), licensee (may wrap), "Name, Administrator", facility
    type, Medicare ID."""
    from pypdf import PdfReader
    return parse_ne_text("\n".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages))


def parse_ne_text(text: str) -> list[dict]:
    starts = list(_NE_HOME_RE.finditer(text))
    rows = []
    for i, m in enumerate(starts):
        block = text[m.end(): starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        lines = [line.strip() for line in block.split("\n")]
        adm = next((j for j, line in enumerate(lines) if re.search(r",\s*(Interim )?Administrator\b", line)), None)
        if adm is None or "(" not in lines:
            continue
        phones = [j for j, line in enumerate(lines[:adm]) if re.fullmatch(r"\d{3}-\d{4}", line)]
        ccn = next((line for line in lines[adm + 1:adm + 6] if re.fullmatch(r"\d{2}[0-9A-Z]\d{3}", line)), None)
        licensee = " ".join(" ".join(lines[phones[-1] + 1:adm]).split()) if phones else ""
        if not ccn or not licensee:
            continue
        rows.append({"ccn": ccn, "licensee": licensee, "licence_date": None, "facility": lines[0],
                     "city": m.group(1).strip(), "address": " ".join(" ".join(lines[1:lines.index("(")]).split())})
    return rows


def parse_mi_json(data: dict) -> list[dict]:
    """scripts/mi_licence_browser.js output: one detail record per home."""
    return [{"ccn": None, "licensee": (r.get("licenseeEntityName") or "").strip(),
             "licence_date": _date(r.get("originalEffectiveDate") or ""),
             "facility": (r.get("licenseName") or "").strip(), "city": (r.get("city") or "").strip(),
             "address": (r.get("addressLine1") or "").strip()}
            for r in data["rows"]
            if (r.get("primaryStatus") or "ACTIVE") == "ACTIVE" and (r.get("licenseeEntityName") or "").strip()]


def parse_oh_csv(text: str) -> list[dict]:
    """ODH's CSV export: records end in a bare carriage return, and the
    page's HTML follows the last one. One row per owner; a home with two
    owners keeps both names."""
    homes = {}
    reader = csv.reader(io.StringIO(text.split("<!DOCTYPE", 1)[0], newline=""))
    header = next(reader)
    if header[:2] != ["NAME", "FACID"]:
        raise ValueError(f"unexpected OH header {header[:4]}")
    for values in reader:
        r = dict(zip(header, values))
        if not r.get("FACID", "").startswith("OHL") or r["OPERSTDESC"] != "ACTIVE" or not r["OWNCOMP"].strip():
            continue
        home = homes.setdefault(r["FACID"], {"ccn": None, "licensee": [], "licence_date": None,
                                             "facility": r["NAME"].strip(), "city": r["FAC_CITY"].strip(),
                                             "address": r["ADDRESS"].strip()})
        owner = " ".join(r["OWNCOMP"].split())
        if owner not in home["licensee"]:
            home["licensee"].append(owner)
    return [{**h, "licensee": " / ".join(sorted(h["licensee"]))} for h in homes.values()]


WA_FORMS_URL = "https://fortress.wa.gov/dshs/adsaapps/lookup/NHForms.aspx?ref=pub&Lic={}"
_WA_ROW_RE = re.compile(
    r'<td valign="top"><strong>(.*?)</strong><br />\s*License #:\s*(\d+)\s*</td>\s*<td>(.*?)</td>', re.S)
_WA_CITY_RE = re.compile(r"^(.+?),\s*WA\s+\d{5}")


def parse_wa_html(page: str) -> list[dict]:
    """Locator results -> rows. The licence number is the diff key, kept in
    "licensee" as "licence #N" so the snapshot table holds it."""
    import html
    rows = []
    for m in _WA_ROW_RE.finditer(page):
        lines = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip()
                 for x in re.sub(r"<a\b.*", "", m.group(3), flags=re.S).split("<br />")]
        lines = [x for x in lines if x]
        city_at = next((i for i, x in enumerate(lines) if _WA_CITY_RE.match(x)), None)
        if not city_at:
            continue
        rows.append({"facility": " ".join(html.unescape(m.group(1)).split()), "licence_no": m.group(2),
                     "licensee": f"licence #{m.group(2)}", "licence_date": None,
                     "address": lines[city_at - 1], "city": _WA_CITY_RE.match(lines[city_at]).group(1)})
    return rows


def fetch_wa_html() -> str:
    """The locator is an ASP.NET form: GET for its tokens, then post
    "Show All Facilities" (criteria 3)."""
    s = cffi_requests.Session(impersonate="chrome")
    page = s.get(LISTS["WA"]["page_url"], timeout=60).text
    form = dict(re.findall(r'name="(__[A-Z]+)" id="__[A-Z]+" value="([^"]*)"', page))
    form.update({"ctl00$content$criteria": "3", "ctl00$content$searchVal": "", "ctl00$content$submit": "Search"})
    resp = s.post(LISTS["WA"]["page_url"], data=form, timeout=180)
    resp.raise_for_status()
    return resp.text


_WA_LICENSEE_RE = re.compile(r"State License #:\s*(\d+)\s*\n?\s*Licensee Information:\s*([^\n]+)")


def wa_licensee_from_letter_text(text: str, licence_no: str) -> str | None:
    m = _WA_LICENSEE_RE.search(text or "")
    if m and m.group(1) == licence_no:
        return " ".join(m.group(2).split()) or None
    return None


def wa_licensee(licence_no: str, max_letters: int = 3) -> tuple[str, str] | tuple[None, None]:
    """(licensee, letter URL) from the home's newest enforcement letters
    that show this licence number; (None, None) if none is posted yet."""
    import io as _io
    from urllib.parse import quote, urljoin
    from pypdf import PdfReader
    try:
        s = cffi_requests.Session(impersonate="chrome")
        page = s.get(WA_FORMS_URL.format(licence_no), timeout=60).text
        links = re.findall(r'href="([^"]*/enforcement letters/(\d{4})/[^"]*\.pdf)"', page, re.I)
        for href, _ in sorted(links, key=lambda x: x[1], reverse=True)[:max_letters]:
            url = urljoin(WA_FORMS_URL, quote(href.replace("&amp;", "&"), safe="/:"))
            resp = s.get(url, timeout=60)
            if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
                continue
            text = PdfReader(_io.BytesIO(resp.content)).pages[0].extract_text() or ""
            name = wa_licensee_from_letter_text(text, licence_no)
            if name:
                return name, url
    except Exception as e:
        logger.warning(f"WA licence #{licence_no}: enforcement letters not read: {e}")
    return None, None


IA_BASE = "https://dia-hfd.iowa.gov"
IA_TYPES = ("10", "30", "31")  # Free Standing NF, SNF, NF/SNF
_COMPANY_RE = re.compile(r"\b(LLC|L\.L\.C|INC|INCORPORATED|CORP|CORPORATION|LP|LLP|LTD|COMPANY|OPCO)\b", re.I)


def parse_ia_json(data: dict) -> list[dict]:
    rows = []
    for x in data.get("data") or []:
        ccn = (x.get("fedNumber") or "").strip()
        if not re.fullmatch(r"16\d{4}", ccn) or (x.get("status") or "") != "Active":
            continue
        name = " ".join((x.get("name") or "").split())
        rows.append({"ccn": ccn, "licensee": name, "licence_date": _date(x.get("currentLicenseDate")),
                     "facility": name, "city": (x.get("city") or "").strip(), "entity_id": x.get("id"),
                     "licence_no": (x.get("classificationNumber") or "").strip() or None})
    return rows


def fetch_ia_json() -> dict:
    s = cffi_requests.Session(impersonate="chrome")
    page = s.get(IA_BASE + "/", timeout=60).text
    tok = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', page).group(1)
    form = [("__RequestVerificationToken", tok), ("draw", "1"), ("start", "0"), ("length", "2000")]
    form += [("TypeVals", t) for t in IA_TYPES]
    resp = s.post(LISTS["IA"]["csv_url"], data=form, timeout=180,
                  headers={"X-Requested-With": "XMLHttpRequest", "RequestVerificationToken": tok})
    resp.raise_for_status()
    return resp.json()


_IA_RELATED_RE = re.compile(r'id="entityName">\s*(.*?)\s*</td>', re.S)


def ia_related(entity_id, get=None) -> list[str]:
    """Names of the homes in the same owner group (the Related Entities tab)."""
    get = get or (lambda url: cffi_requests.get(url, impersonate="chrome", timeout=60).text)
    try:
        page = get(f"{IA_BASE}/Home/SwitchToTab/{entity_id}?tabname=RelatedEntities")
    except Exception as e:
        logger.warning(f"IA related entities {entity_id}: {e}")
        return []
    return [" ".join(re.sub(r"\([^()]*\)$", "", n).split()) for n in _IA_RELATED_RE.findall(page)]


def ia_new_licence(row: dict, prev_date) -> bool:
    """A renewal keeps the month and day; a sale's new licence starts on the
    sale date."""
    d = row["licence_date"]
    return bool(d and prev_date and d != prev_date and (d.month, d.day) != (prev_date.month, prev_date.day))


def parse_browser_file(data: dict) -> tuple[str, list[dict]]:
    """A saved scripts/{mi,oh}_licence_browser.js result -> (state, rows)."""
    if data.get("state") == "MI":
        return "MI", parse_mi_json(data)
    if data.get("state") == "OH":
        return "OH", parse_oh_csv(data["csv"])
    raise ValueError(f"not a licence-list browser file (state={data.get('state')!r})")


def record_url(state: str, row: dict) -> str:
    if state == "IA":  # the name is often the unchanged DBA: key on the new licence's date
        return f"{LISTS[state]['page_url']}#licence-{row['ccn']}-{row['licence_date']:%Y-%m-%d}"
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
        if prev is not None and state == "WA":  # a licence number: any change is a new licence
            names = [n for n in cms_names[row["ccn"]] if not _ADDRESS_RE.match(n)]
            changed, seller = row["licensee"] != prev, (names[0] if names else None)
        elif state == "IA" and prev is None:  # every licence is under a year old: baseline only
            changed, seller = False, None
        elif state == "IA":
            names = [n for n in cms_names[row["ccn"]] if not _ADDRESS_RE.match(n)]
            changed = ia_new_licence(row, row.get("prev_licence_date"))
            seller = names[0] if names else None
            if changed:
                row = {**row, "prev_name": prev,
                       "buyer": row["licensee"] if (_COMPANY_RE.search(row["licensee"])
                                                    and not any(same_entity(row["licensee"], n) for n in names))
                       else None}
        elif prev is not None:
            changed, seller = not same_entity(row["licensee"], prev), prev
        else:
            names = [n for n in cms_names[row["ccn"]] if not _ADDRESS_RE.match(n)]
            changed = (bool(names) and bool(row["licence_date"]) and row["licence_date"] >= seed_since
                       and not any(same_entity(row["licensee"], n) for n in names))
            seller = names[0] if names else None
        if changed and any(same_entity(row.get("buyer") or row["licensee"], b)
                           for b in chow_buyers.get(row["ccn"], ())):
            changed = False  # CMS's CHOW file already has this sale
        if changed and not is_known(record_url(state, row)):
            changes.append({**row, "seller": seller})
            continue  # snapshot moves on once the record is stored
        snapshot.append(row)
    return changes, snapshot


def _wa_deal(row: dict) -> dict:
    cfg, buyer = LISTS["WA"], row.get("buyer")
    seller = row.get("seller") if row.get("seller") and not (buyer and same_entity(buyer, row["seller"])) else None
    summary = (
        f"{cfg['label']} shows {row['facility']} ({row['city'].title()}) under a new state licence, "
        f"#{row['licence_no']}" + (f" (was #{row['prev_licence_no']})" if row.get("prev_licence_no") else "")
        + ". A Washington nursing home licence can't be transferred, so a new number means a new "
          "licensee, usually a sale or new operator"
        + (f"; DSHS's latest letter to the home names the licensee as {buyer}" if buyer else
           "; no DSHS letter naming the new licensee is posted yet")
        + (f"; CMS lists {seller} as the legal owner" if seller else "")
        + ". The list gives no date or price."
    )
    url = record_url("WA", row)
    return {
        "url": url,
        "title": f"[WA licence] {row['facility']}: new licence #{row['licence_no']}"
                 + (f", licensee {buyer}" if buyer else ""),
        "published_at": datetime.now(timezone.utc),
        "raw_text": f"{summary}\n\nSource: {cfg['page_url']}"
                    + (f"\nLetter: {row['letter_url']}" if row.get("letter_url") else ""),
        "source_type": "con",
        "pre_extracted": True,
        "extraction_model": "con_direct",
        "_con_id": url.rsplit("#", 1)[1],
        "acquiring_entity": buyer,
        "seller_entity": seller,
        "operator_names": [buyer] if buyer else [],
        "facility_names": [row["facility"]],
        "ccns": [row["ccn"]],
        "states": ["WA"],
        "facility_count": 1,
        "deal_value_m": None,
        "acquisition_date": None,
        "financing_amount_m": None,
        "lender": None,
        "rationale": summary,
    }


def _ia_deal(row: dict) -> dict:
    cfg, buyer = LISTS["IA"], row.get("buyer")
    seller = row.get("seller") if row.get("seller") and not (buyer and same_entity(buyer, row["seller"])) else None
    related = [n for n in row.get("related") or [] if not same_entity(n, row["licensee"])]
    renamed = row.get("prev_name") and not same_entity(row["prev_name"], row["licensee"])
    summary = (
        f"{cfg['label']} shows {row['licensee']} ({row['city'].title()}) under a new licence dated "
        f"{row['licence_date']:%m/%d/%Y}"
        + (f" (the previous one ran from {row['prev_licence_date']:%m/%d/%Y})" if row.get("prev_licence_date") else "")
        + (f", and the home was listed as {row['prev_name']} before" if renamed else "")
        + ". Iowa licences renew on the same date each year, so a licence starting on a new date "
          "usually means a sale or a new operator"
        + (f"; the licensee is listed as {buyer}" if buyer else "; the list doesn't name the new licensee")
        + (f"; CMS lists {seller} as the legal owner" if seller else "")
        + (f"; DIAL groups it with {', '.join(related[:4])}" + (f" and {len(related) - 4} more" if len(related) > 4 else "")
           if related else "")
        + ". No price is given."
    )
    url = record_url("IA", row)
    when = row["licence_date"]
    return {
        "url": url,
        "title": f"[IA licence] {row['licensee']}: new licence {when:%m/%d/%Y}" + (f", licensee {buyer}" if buyer else ""),
        "published_at": datetime.combine(when, datetime.min.time()).replace(tzinfo=timezone.utc),
        "raw_text": f"{summary}\n\nSource: {cfg['page_url']}Home/PublicEntityDetails?recordid={row.get('entity_id')}",
        "source_type": "con",
        "pre_extracted": True,
        "extraction_model": "con_direct",
        "_con_id": url.rsplit("#", 1)[1],
        "acquiring_entity": buyer,
        "seller_entity": seller,
        "operator_names": [buyer] if buyer else [],
        "facility_names": [row["facility"]],
        "ccns": [row["ccn"]],
        "states": ["IA"],
        "facility_count": 1,
        "deal_value_m": None,
        "acquisition_date": when.isoformat(),
        "financing_amount_m": None,
        "lender": None,
        "rationale": summary,
    }


def to_deal(state: str, row: dict) -> dict:
    if state == "WA":
        return _wa_deal(row)
    if state == "IA":
        return _ia_deal(row)
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
        if state == "TX":
            from scraper.tx_tulip import fetch_tx_rows
            rows = fetch_tx_rows()
            if len(rows) < 1000:  # 1,281 homes on 2026-11-05
                health.source_failed("TX licence list", f"only {len(rows)} homes with an owner (layout change?)")
                return None
            return rows
        if state == "NC":
            resp = cffi_requests.get(cfg["csv_url"], impersonate="chrome", timeout=180)
            resp.raise_for_status()
            rows = parse_nc_xlsx(resp.content)
            if len(rows) < 300:  # 427 homes in the 10/2026 list
                health.source_failed("NC licence list", f"only {len(rows)} homes (layout change?)")
                return None
            return rows
        if state == "WI":
            resp = cffi_requests.get(cfg["csv_url"], impersonate="chrome", timeout=180)
            resp.raise_for_status()
            rows = parse_wi_xlsx(resp.content)
            if len(rows) < 250:  # 330 homes in the 10/2026 directory
                health.source_failed("WI licence list", f"only {len(rows)} homes (layout change?)")
                return None
            return rows
        if state == "NE":
            resp = cffi_requests.get(cfg["csv_url"], impersonate="chrome", timeout=180)
            resp.raise_for_status()
            rows = parse_ne_pdf(resp.content)
            if len(rows) < 140:  # 169 homes with a Medicare ID in the 10/2026 roster
                health.source_failed("NE licence list", f"only {len(rows)} homes (layout change?)")
                return None
            return rows
        if state == "WA":
            rows = parse_wa_html(fetch_wa_html())
            if len(rows) < 150:  # 195 homes on 2026-11-10
                health.source_failed("WA licence list", f"only {len(rows)} homes (layout change?)")
                return None
            return rows
        if state == "IA":
            rows = parse_ia_json(fetch_ia_json())
            if len(rows) < 300:  # 377 nursing facilities on 2026-11-10
                health.source_failed("IA licence list", f"only {len(rows)} homes (layout change?)")
                return None
            return rows
        if state == "KS":
            rows = parse_ks_csv(fetch_ks_csv())
            if len(rows) < 110:  # 147 nursing facilities with a DBA, 11/2026
                health.source_failed("KS licence list", f"only {len(rows)} homes (layout change?)")
                return None
            return rows
        if state == "KY":
            resp = cffi_requests.get(cfg["csv_url"], impersonate="chrome", timeout=180)
            resp.raise_for_status()
            rows = parse_ky_xlsx(resp.content)
            if len(rows) < 200:  # 280+ nursing homes in the 11/2026 directory
                health.source_failed("KY licence list", f"only {len(rows)} homes (layout change?)")
                return None
            return rows
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


def _wa_fill_buyers(changes: list[dict], previous: dict, chow_buyers: dict, snapshot: list[dict],
                    lookup=None) -> list[dict]:
    """Buyer from DSHS's letters; a buyer CMS's CHOW file already names is
    left to CHOW (the home's snapshot moves on)."""
    lookup = lookup or wa_licensee
    kept = []
    for row in changes:
        row["prev_licence_no"] = (previous.get(row["ccn"]) or "").replace("licence #", "") or None
        row["buyer"], row["letter_url"] = lookup(row["licence_no"])
        if row["buyer"] and any(same_entity(row["buyer"], b) for b in chow_buyers.get(row["ccn"], ())):
            snapshot.append(row)
            continue
        kept.append(row)
    return kept


def licence_list_deals(state: str, conn, is_known, rows: list[dict] | None = None,
                       write_snapshot: bool = True) -> list[dict]:
    """Diff one state's list against the stored snapshot; write the snapshot
    rows that are settled; return the new-licensee records. rows: a list
    read elsewhere (BROWSER_STATES); fetched here when None."""
    if rows is None and state in BROWSER_STATES:
        return []
    if state == "TX" and rows is None:
        with conn.cursor() as cur:
            cur.execute("""SELECT max(seen_at) > now() - make_interval(days => %s)
                           FROM licence_list_snapshot WHERE state = 'TX'""", (TX_REREAD_DAYS,))
            if cur.fetchone()[0]:
                return []
    if rows is None:
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
        if state == "IA":
            cur.execute("SELECT ccn, licence_date FROM licence_list_snapshot WHERE state = 'IA'")
            prev_dates = dict(cur.fetchall())
            for r in rows:
                r["prev_licence_date"] = prev_dates.get(r["ccn"])
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
    if state == "WA":
        changes = _wa_fill_buyers(changes, previous, chow_buyers, snapshot)
    if state == "IA":
        for row in changes:
            row["related"] = ia_related(row["entity_id"])
    if not write_snapshot:
        snapshot = []
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
