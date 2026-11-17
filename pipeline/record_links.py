"""Source links that open the record a card is about, not a search page.

The stored article URLs stay as they are (they're dedup keys:
"...#ccn-525317-02-28-2025", "chow://ccn-...", "...#licensee-105597-...");
export_deals rewrites what the site links to (user rule 2026-11-17: a
person opening a source should land on that home, filing or paragraph).

    record_url(url, deal, text=None) -> str
"""
import json
import re
from pathlib import Path
from urllib.parse import quote, urlencode

CHOW_API = "https://data.cms.gov/data-api/v1/dataset/1e1afa09-5699-46a5-ae9e-47017397c55c/data"
OWNERS_API = "https://data.cms.gov/provider-data/api/1/datastore/query/y2hd-n93e/0"
NY_CON_API = "https://health.data.ny.gov/resource/h343-jwie.json"
MO_LTC_API = "https://data.mo.gov/resource/fenu-sipv.json"
FL_PROFILE = "https://quality.healthfinder.fl.gov/Facility-Provider/Profile/?LID={}"
FL_LIDS = Path(__file__).resolve().parent.parent / "data" / "fl_licence_ids.json"   # CCN -> LicenseID

_CHOW_HASH = re.compile(r"#ccn-(\d{6}[A-Z]?)-(\d{2})-(\d{2})-(\d{4})$")
_CHOW_KEY = re.compile(r"^chow://ccn-(\d{6})-(\d{2})(\d{2})(\d{4})$")
_LICENSEE = re.compile(r"#licensee-([0-9A-Z]{6})-")
_fl_lids = None


def _chow(ccn: str, mm: str, dd: str, yyyy: str) -> str:
    return CHOW_API + "?" + urlencode({"filter[CCN - BUYER]": ccn, "filter[EFFECTIVE DATE]": f"{yyyy}-{mm}-{dd}"})


def _owner(ccn: str, owner: str | None) -> str:
    q = {"conditions[0][property]": "cms_certification_number_ccn", "conditions[0][value]": ccn}
    if owner:
        q |= {"conditions[1][property]": "owner_name", "conditions[1][value]": owner}
    return OWNERS_API + "?" + urlencode(q)


def _fl(ccn: str) -> str | None:
    global _fl_lids
    if _fl_lids is None:
        try:
            _fl_lids = json.loads(FL_LIDS.read_text())
        except (OSError, ValueError):
            _fl_lids = {}
    lid = _fl_lids.get(ccn)
    return FL_PROFILE.format(lid) if lid else None


def text_fragment(text: str | None, *terms: str | None) -> str:
    """'#:~:text=...' (Chrome, Edge, Safari scroll to and highlight it): the
    first term the article names at most 3 times, so a roundup opens at its deal."""
    if not text:
        return ""
    low = text.lower()
    for t in terms:
        t = " ".join((t or "").split())
        # a name the article repeats (the filer's own name) doesn't find the deal
        if len(t) >= 6 and 1 <= low.count(t.lower()) <= 3:
            return "#:~:text=" + quote(t, safe="")
    return ""


def record_url(url: str | None, deal: dict, text: str | None = None) -> str | None:
    if not url:
        return url
    ccns = deal.get("ccns") or []
    if m := _CHOW_HASH.search(url):
        return _chow(m.group(1), m.group(2), m.group(3), m.group(4))
    if m := _CHOW_KEY.match(url):
        return _chow(*m.groups())
    if "y2hd-n93e#owner-" in url and ccns:
        return _owner(ccns[0], deal.get("acquiring_entity") or deal.get("buyer"))
    if "h343-jwie#project-" in url:
        return NY_CON_API + "?" + urlencode({"con_project_number": url.rsplit("#project-", 1)[1]})
    if "fenu-sipv#licensee-" in url and deal.get("facility_names"):
        return MO_LTC_API + "?" + urlencode({"facility_name": deal["facility_names"][0]})
    if "healthfinder.fl.gov" in url and (m := _LICENSEE.search(url)):
        return _fl(m.group(1)) or url
    if "#" in url and (doc := document_url(url, deal, text)):
        return doc
    if ".pdf#project-" in url.lower():        # NY PHHPC agenda: the page with the project
        base, proj = url.split("#project-", 1)
        return pdf_page(base, [proj] + list(deal.get("facility_names") or []))
    if "#" in url and ":~:" not in url and (a := anchor_url(url, text)):
        return a
    if url.lower().split("?")[0].endswith(".pdf") and "#" not in url:
        return pdf_page(url, deal.get("facility_names") or [])
    if url.startswith("http") and "#" not in url and text:
        names = list(deal.get("facility_names") or [])
        return url + text_fragment(text, *names, deal.get("acquiring_entity"), deal.get("seller_entity"))
    return url


# ── State notices: the document itself, at the page naming the home ─────
_SOURCE = re.compile(r"Source: (https?://\S+)")
PDF_PAGES = Path(__file__).resolve().parent.parent / "data" / "pdf_page_cache.json"   # url -> [page text]
_pdf_cache = None


def _pages(pdf_url: str) -> list[str]:
    """Each page's text, lower-cased (downloaded once, kept in data/)."""
    global _pdf_cache
    if _pdf_cache is None:
        try:
            _pdf_cache = json.loads(PDF_PAGES.read_text())
        except (OSError, ValueError):
            _pdf_cache = {}
    if pdf_url not in _pdf_cache:
        import io
        from curl_cffi import requests as cffi
        from pypdf import PdfReader
        try:
            r = cffi.get(pdf_url, impersonate="chrome", timeout=60)
            r.raise_for_status()
            _pdf_cache[pdf_url] = [(p.extract_text() or "").lower() for p in PdfReader(io.BytesIO(r.content)).pages]
        except Exception:
            _pdf_cache[pdf_url] = []
        PDF_PAGES.write_text(json.dumps(_pdf_cache))
    return _pdf_cache[pdf_url]


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def document_url(url: str, deal: dict, text: str | None) -> str | None:
    """A state record whose text names the exact document it came from
    ('Source: https://.../AugustNotice2026.pdf'): that document, a PDF opened
    at the first page naming the record's number or home."""
    m = _SOURCE.search(text or "")
    if not m or m.group(1).split("#")[0] == url.split("#")[0]:
        # a list page as the source: the letter or PDF naming the home (WA DSHS letters)
        m = re.search(r"(https?://\S+?\.pdf)", text or "", re.I)
    if not m or m.group(1).split("#")[0] == url.split("#")[0]:
        return None
    doc = m.group(1).rstrip(".,;)")
    if ".pdf" not in doc.lower():
        return doc
    key = url.rsplit("#", 1)[-1] if "#" in url else ""
    num = re.search(r"\d{2,4}-\d{2,5}[A-Z]?", key)
    return pdf_page(doc, ([num.group(0)] if num else []) + list(deal.get("facility_names") or []))


def pdf_page(doc: str, terms: list[str]) -> str:
    """The PDF opened at the first page naming one of terms (only when it
    has more than one page)."""
    pages = [_norm(p) for p in _pages(doc)]
    if len(pages) > 1:
        for t in terms:
            t = _norm(t)
            for i, p in enumerate(pages, 1):
                if t and t in p:
                    return f"{doc}#page={i}"
    return doc


# ── RI's Google Sheet: the applicant's row ───────────────────────────────
RI_SHEET = "1fdRP2rh34eluTT13k8EIqjJUSnGTH2U5yGD-KwRFi-M"
RI_TABS = (833901994, 1595076143, 1075367567)          # 2026, 2025, 2024
_ri_rows = None


def _ri(applicant_slug: str) -> str | None:
    global _ri_rows
    if _ri_rows is None:
        import csv, io
        from curl_cffi import requests as cffi
        _ri_rows = []
        for gid in RI_TABS:
            try:
                r = cffi.get(f"https://docs.google.com/spreadsheets/d/{RI_SHEET}/export?format=csv&gid={gid}",
                             impersonate="chrome", timeout=60)
                for n, row in enumerate(csv.reader(io.StringIO(r.text)), 1):
                    _ri_rows.append((gid, n, _norm(" ".join(row))))
            except Exception:
                pass
    want = _norm(applicant_slug)
    # a long slug can be cut short; its first words still find the row
    for w in (want, " ".join(want.split()[:4]), " ".join(want.split()[:2])):
        for gid, n, text in _ri_rows:
            if w and text.startswith(w) or (len(w) > 12 and w in text):
                return f"https://docs.google.com/spreadsheets/d/{RI_SHEET}/edit#gid={gid}&range=A{n}"
    return None


def anchor_url(url: str, text: str | None) -> str | None:
    """An index page with '#<record key>': the page highlighting the
    record's number (NC exemption 5123, Hawaii application 24-04A)."""
    key = url.rsplit("#", 1)[1]
    if "docs.google.com" in url and (m := re.match(r"cec-(.+?)(?:-\d{6,8})?$", key)):
        return _ri(m.group(1).replace("-", " "))
    num = re.search(r"(?:exemption|app|review)-(\d[\w-]*)$", key)
    if num and text and num.group(1) in text:
        return url.split("#")[0] + "#:~:text=" + quote(num.group(1), safe="")
    return None
