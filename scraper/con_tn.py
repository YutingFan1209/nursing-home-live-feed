"""
Tennessee Health Facilities Commission -- nursing home CHOWs ratified each month.

A Tennessee nursing home sale needs a new licence: the buyer files a "Nursing
Home Application for Change of Ownership", and the Commission ratifies it at
its monthly meeting, listed in the agenda's licensure consent calendar under
"Qualifying Applications - (CHOWS) - Nursing Homes" ("Etowah Health and
Rehabilitation, Etowah - File #163"). Agendas stay posted at
Agenda-<Month><Year>.pdf, so each run reads the last LOOKBACK_MONTHS of them.
For the upcoming meeting the monthly page also links each application
(scanned, transcribed by pipeline/pdf_text.py), which names the new licensee,
its holding company and the building owner -- the buyer. Older applications
aren't linked anywhere, so earlier months give the home only.

tn.gov answers only a real browser (403 to scripts), so pages and PDFs are
read in the auto-launched Chrome over CDP, like PA's UCC search
(ucc/chrome_cdp.py); the person approved that on 2026-10-27. The "Meeting
Results" PDFs on the previous-meetings page cover CON votes only, not CHOWs.
See docs/con-feasibility.md (Tennessee).
"""

import base64
import logging
import re
from datetime import date, datetime, timezone

from rapidfuzz import fuzz

from pipeline.pdf_text import pdf_text
from pipeline.run_health import health

logger = logging.getLogger(__name__)

CON_TN_SOURCE_NAME = "Tennessee HFC Monthly Meeting Agendas (CHOWs)"
CON_TN_INDEX_URL = "https://www.tn.gov/hfc/commission-meeting-information/monthly-meeting-information.html"
AGENDA_URL = "https://www.tn.gov/content/dam/tn/hfc/documents/Agenda-{month}{year}.pdf"
LOOKBACK_MONTHS = 12
FETCH_GAP_MS = 2000

_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]
_MEETING_DATE_RE = re.compile(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday),\s+(" + "|".join(_MONTHS)
                              + r")\s+(\d{1,2}),\s+(\d{4})")
# A CHOW subsection runs from its heading to the next numbered/lettered item
_CHOW_SECTION_RE = re.compile(r"\(CHOW[’']?S\)(.*?)(?=\n\s*(?:\d\.\s+Applications|[A-Z]\.\s+License Status|"
                              r"[A-Z]\.\s+Disciplinary)|\Z)", re.S)
_SUBHEAD_RE = re.compile(r"(?m)^\s*[a-zA-Z]\.\s+(.+?)\s*$")
# "<home>, <town> - File #163"; the town is sometimes left out, and December
# 2025's list is numbered ("1. ... - Lic. No. 389 2. ...")
_ENTRY_END_RE = re.compile(r"\s*[-–]+\s*(?:File|License|Lic\.?)\s*(?:#|No\.?)\s*(\d+)\s*")
_TOWN_RE = re.compile(r"^(?P<name>.+),\s*(?P<town>[A-Z][A-Za-z .']{2,30})$")
_AKA_RE = re.compile(r"\s+(?:fka|f/k/a|dba|d/b/a|aka)\s+", re.I)


def agenda_months(today: date, lookback: int = LOOKBACK_MONTHS) -> list[tuple[str, int]]:
    out, y, m = [], today.year, today.month
    for _ in range(lookback + 1):
        out.append((_MONTHS[m - 1], y))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def meeting_date(agenda_text: str):
    m = _MEETING_DATE_RE.search(agenda_text)
    if not m:
        return None
    return datetime(int(m.group(3)), _MONTHS.index(m.group(1)) + 1, int(m.group(2)), tzinfo=timezone.utc)


def parse_nursing_home_chows(agenda_text: str) -> list[dict]:
    """Nursing home entries in the agenda's CHOW sections (the regular one
    and "Special Consideration"); other facility types are skipped."""
    rows = []
    for section in _CHOW_SECTION_RE.findall(agenda_text):
        heads = list(_SUBHEAD_RE.finditer(section))
        for i, h in enumerate(heads):
            if "nursing home" not in h.group(1).lower():
                continue
            body = section[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(section)]
            body = " ".join(re.sub(r"(?m)^\s*\d+\s*$", "", body).split())
            parts = _ENTRY_END_RE.split(body)
            for head, num in zip(parts[0::2], parts[1::2]):
                head = re.sub(r"^\d+\.\s+", "", head.strip())
                m = _TOWN_RE.match(head)
                name, town = (m.group("name"), m.group("town").strip()) if m else (head, None)
                rows.append({"facility": name.strip(" ,"), "town": town, "number": num})
    return rows


def _field(text: str, label_re: str) -> str | None:
    m = re.search(label_re + r"\s*:?\s*\**\s*Name:?\**\s*(.+?)(?:\s{2,}|\s+Phone|\n|$)", text, re.I | re.S)
    if not m:
        return None
    name = m.group(1).strip(" .*_")
    return None if not name or name.upper() in ("N/A", "NA", "SAME", "SAME AS FACILITY") else name


def parse_application(text: str) -> dict:
    """Parties on the CHOW application form: the legal entity (1.c, the new
    licensee), its holding company (6.b) and the building owner."""
    return {
        "licensee": _field(text, r"Legal Entity checked in 1\.?\s*a"),
        "holding": _field(text, r"list the name, address, and phone number of the holding company"),
        "building_owner": _field(text, r"Ownership of Building"),
    }


_NAME_SUBS = [(r"&", " AND "), (r"\bREHAB\b", "REHABILITATION"), (r"\bCTR\b", "CENTER"),
              (r"\bHEALTH CARE\b", "HEALTHCARE"), (r"\b(?:LLC|INC|THE)\b", " "), (r"[^A-Z0-9 ]", " ")]


def _name(s: str) -> str:
    s = (s or "").upper()
    for a, b in _NAME_SUBS:
        s = re.sub(a, b, s)
    return " ".join(s.split())


def match_ccn(row: dict, cms_homes: list[tuple[str, str, str]]) -> str | None:
    """CCN of the one CMS home whose name agrees with the agenda's (either
    side of a "fka"/"dba"): in the row's town, or statewide when the agenda
    gives no town. CMS abbreviates ("WYNDRIDGE HEALTH AND REHAB CTR")."""
    names = [_name(n) for n in _AKA_RE.split(row["facility"]) if n]
    town = (row["town"] or "").upper()
    # Whole-name score: Etowah's "Health and Rehabilitation" must not also
    # match "STARR REGIONAL HEALTH & REHABILITATION" in the same town
    scored = sorted(((max(fuzz.token_sort_ratio(n, _name(name)) for n in names), ccn)
                     for name, city, ccn in cms_homes if not town or (city or "").upper() == town), reverse=True)
    if not scored or scored[0][0] < (85 if town else 92):
        return None
    return scored[0][1] if len(scored) == 1 or scored[1][0] < scored[0][0] else None


def record_url(row: dict, meeting: datetime | None, month: str, year: int) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", _AKA_RE.split(row["facility"])[0].lower()).strip("-")
    when = f"{meeting:%Y%m%d}" if meeting else f"{year}{_MONTHS.index(month) + 1:02d}"
    return f"{CON_TN_INDEX_URL}#chow-{key}-{when}"


def to_deal(row: dict, meeting: datetime | None, agenda_url: str, key: str, ccn: str | None,
            parties: dict | None, letter_url: str | None) -> dict:
    parties = parties or {}
    buyer = parties.get("licensee")
    held = parties.get("holding")
    summary = (
        f"Tennessee Health Facilities Commission agenda"
        + (f" for {meeting:%m/%d/%Y}" if meeting else "")
        + f": change of ownership (CHOW) licence application for {row['facility']}"
        + (f", {row['town']}" if row["town"] else "") + " "
          f"(#{row['number']}), up for ratification"
        + (f". New licensee: {buyer}" if buyer else "")
        + (f", held by {held}" if held and held != buyer else "")
        + (f"; building owner {parties['building_owner']}" if parties.get("building_owner") else "")
        + ("." if buyer else ". The agenda doesn't name the buyer.")
    )
    sources = f"Source: {agenda_url}" + (f"\nApplication: {letter_url}" if letter_url else "")
    return {
        "url": key,
        "title": f"[TN CHOW] {row['facility']}" + (f" ({row['town']})" if row["town"] else "")
                 + (f": {buyer}" if buyer else ""),
        "published_at": meeting or datetime.now(timezone.utc),
        "raw_text": f"{summary}\n\n{sources}",
        "source_type": "con",
        "pre_extracted": True,
        "extraction_model": "con_direct",
        "_con_id": key.rsplit("#", 1)[1],
        "acquiring_entity": buyer,
        "seller_entity": None,
        "operator_names": [buyer] if buyer else [],
        "facility_names": [_AKA_RE.split(row["facility"])[0]],
        # Linked by name + town to one CMS home, not by owner names
        **({"ccns": [ccn]} if ccn else {}),
        "states": ["TN"],
        "facility_count": 1,
        "deal_value_m": None,
        "acquisition_date": meeting.date().isoformat() if meeting else None,
        "financing_amount_m": None,
        "lender": None,
        "rationale": summary,
    }


def _fetch_bytes(page, url: str) -> bytes | None:
    """GET from inside the tn.gov tab (same origin, real browser)."""
    status, b64 = page.evaluate("""async u => {
        const r = await fetch(u);
        const b = new Uint8Array(await r.arrayBuffer());
        let s = ''; for (let i = 0; i < b.length; i += 8192) s += String.fromCharCode(...b.subarray(i, i + 8192));
        return [r.status, btoa(s)]; }""", url)
    data = base64.b64decode(b64)
    page.wait_for_timeout(FETCH_GAP_MS)
    return data if status == 200 and data[:4] == b"%PDF" else None


def _pdf_layer_text(data: bytes) -> str:
    import io
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


def fetch_con_tn_deals(is_known, cms_homes: list[tuple[str, str, str]], today: date | None = None) -> list[dict]:
    """cms_homes: (provider_name, provider_city, ccn) of every TN home in CMS."""
    from playwright.sync_api import sync_playwright
    from ucc.chrome_cdp import CDP_URL, ensure_chrome_cdp

    try:
        ensure_chrome_cdp()
    except Exception as e:
        health.source_failed("TN CON agendas", f"Chrome didn't start: {e}")
        return []
    deals, seen = [], set()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        try:
            resp = page.goto(CON_TN_INDEX_URL, timeout=60000)
            page.wait_for_timeout(3000)
            if not resp or resp.status != 200:
                health.source_failed("TN CON agendas", f"monthly meeting page answered {resp.status if resp else None}")
                return []
            links = page.eval_on_selector_all("a", "as => as.map(a => [a.innerText.trim(), a.href])")
            current_agenda = next((h for t, h in links if re.search(r"/Agenda-[A-Za-z]+\d{4}\.pdf$", h)), None)
            pdf_links = [(t, h) for t, h in links if h.lower().endswith(".pdf")]

            urls = [AGENDA_URL.format(month=m, year=y) for m, y in agenda_months(today or date.today())]
            if current_agenda and current_agenda not in urls:
                urls.insert(0, current_agenda)
            read = 0
            for agenda_url in urls:
                month, year = re.search(r"Agenda-([A-Za-z]+)(\d{4})\.pdf", agenda_url).groups()
                try:
                    data = _fetch_bytes(page, agenda_url)
                except Exception as e:
                    logger.warning(f"TN agenda {month} {year}: {e}")
                    continue
                if not data:
                    continue  # no meeting that month
                read += 1
                text = _pdf_layer_text(data)
                meeting = meeting_date(text)
                for row in parse_nursing_home_chows(text):
                    key = record_url(row, meeting, month, year)
                    if key in seen or is_known(key):
                        continue
                    seen.add(key)
                    parties, letter_url = None, None
                    if agenda_url == current_agenda:
                        name = _AKA_RE.split(row["facility"])[0]
                        letter = max(pdf_links, key=lambda l: fuzz.token_set_ratio(name.upper(), l[0].upper()),
                                     default=None)
                        if letter and fuzz.token_set_ratio(name.upper(), letter[0].upper()) >= 90:
                            letter_url = letter[1]
                            try:
                                app = _fetch_bytes(page, letter_url)
                                if app:
                                    app_text, _ = pdf_text(app, max_pages=3, label=f"TN CHOW {name}")
                                    parties = parse_application(app_text)
                            except Exception as e:
                                logger.warning(f"TN CHOW application {letter_url}: {e}")
                                health.failed("TN CHOW applications", f"{letter_url}: {e}")
                    deals.append(to_deal(row, meeting, agenda_url, key, match_ccn(row, cms_homes),
                                         parties, letter_url))
            if not read:
                health.source_failed("TN CON agendas", "no agenda PDFs could be read")
        finally:
            try:
                page.close()
            except Exception:
                pass
    logger.info(f"TN CON: {len(deals)} new nursing home CHOWs")
    return deals
