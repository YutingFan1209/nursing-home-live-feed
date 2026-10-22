"""
Read-only tools the resolver agent can call. Each wraps data or logic the
pipeline already has; nothing here writes to the database.

  search_cms_facilities     fuzzy facility lookup (CMS name / licensed company)
  search_cms_owners         fuzzy owner lookup -> facilities that owner holds
  search_chow_records       CMS change-of-ownership records by buyer OR seller name
  search_cms_by_address     facilities at a street number (debtors named for an address)
  get_facility_ownership    owners, CHOW events, and the deterministic timing
                            rule from pipeline/ucc_category.py
  get_related_ucc_filings   other filings against the same debtor
  search_deals              deals already in the feed (news, CHOW, EDGAR, CON)
  classify_lender           ucc/lender_classifier.py on a secured party, plus the
                            site's own lender rules from pipeline/deal_labels.py

The date arithmetic is deliberately NOT left to the model: get_facility_ownership
calls the same _cms_signal() / owner_start_dates() / complete_through() that
scripts/export_deals.py uses to label deals.

Queries use pg_trgm, which db/schema.sql already indexes. Name search uses word_similarity (`<%`)
plus ILIKE, not whole-string similarity: the latter scores a short query like "North Dade" far
below a long CMS name like "NORTH DADE NURSING AND REHABILITATION CENTER" and finds nothing.
In psycopg2, a literal % in a query that takes parameters must be written %%.
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from pipeline.deal_labels import _HOME_BANK_RE, SMALL_BUSINESS_LENDER_RE
from pipeline.ucc_category import ACQUISITION_WINDOW, _cms_signal, complete_through, owner_start_dates
from ucc.lender_classifier import classify_secured_party, to_confidence_label

MAX_ROWS = 10
# The CMS CHOW file, downloaded once (the database keeps CHOW buyers only, not sellers)
CHOW_CACHE = Path(__file__).resolve().parent.parent / "data" / "chow_cache"
_LEGAL = re.compile(r"\b(llc|l l c|inc|corp|corporation|company|co|lp|l p|ltd|the)\b")
_STREET = {"road": "rd", "street": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd", "drive": "dr",
           "lane": "ln", "highway": "hwy", "parkway": "pkwy", "place": "pl", "court": "ct",
           "north": "n", "south": "s", "east": "e", "west": "w"}
# Words in an address-named debtor that aren't part of the address
_NOT_ADDRESS = re.compile(r"\b(llc|l l c|inc|corp|lp|holdings?|holdco|propco|opco|realty|real estate|"
                          r"property|properties|operating|operations|company|owner|tenant|snf|"
                          r"healthcare|nursing|rehab\w*|center)\b")

# Callable through ToolBox.call and the MCP server, but not offered to the
# resolver agent (it is handed the filing in its prompt).
EXTRA_TOOLS = {"get_ucc_filing"}


def _norm(name: str) -> str:
    t = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower().replace("&", " and "))
    return " ".join(_LEGAL.sub(" ", t).split())


def _street_words(text: str) -> list:
    t = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    return [_STREET.get(w, w) for w in t.split()]


def _day(s) -> Optional[date]:
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _mdy(s) -> Optional[date]:
    try:
        return datetime.strptime(str(s), "%m/%d/%Y").date()
    except ValueError:
        return None


TOOL_SCHEMAS = [
    {
        "name": "search_cms_facilities",
        "description": (
            "Fuzzy-search CMS-certified nursing homes by facility name or licensed company name. "
            "Returns CCN, name, licensed company, city, state, beds, rating, best matches first. "
            "Search the distinctive core of a name ('Havencrest'), not the full LLC string, and "
            "pass the filing's state."
        ),
        "input_schema": {"type": "object", "properties": {
            "name": {"type": "string"}, "state": {"type": ["string", "null"], "description": "2-letter state"},
        }, "required": ["name"]},
    },
    {
        "name": "search_cms_owners",
        "description": (
            "Fuzzy-search CMS ownership records by owner name (a holding company or operator). "
            "Returns the facilities that owner is listed on, with role and start date. Use when "
            "the debtor looks like a holding company rather than a facility."
        ),
        "input_schema": {"type": "object", "properties": {
            "owner_name": {"type": "string"}, "state": {"type": ["string", "null"]},
        }, "required": ["owner_name"]},
    },
    {
        "name": "search_chow_records",
        "description": (
            "Search CMS change-of-ownership (CHOW) records by company name, as BUYER or SELLER. "
            "Use when a debtor isn't a current CMS owner: a company that SOLD a home appears here "
            "as the seller, which ties it to that CCN for the years before the sale. Returns CCN, "
            "effective date, buyer and seller names, and which side matched."
        ),
        "input_schema": {"type": "object", "properties": {
            "name": {"type": "string"}, "state": {"type": ["string", "null"]},
        }, "required": ["name"]},
    },
    {
        "name": "search_cms_by_address",
        "description": (
            "Find CMS facilities at a street address. Use when the debtor is named for an address "
            "('5860 College Rd Holdings LLC', '520 Kerr Avenue Opco'). Pass the address part. "
            "Returns facilities whose street number matches, ranked by how many street words agree."
        ),
        "input_schema": {"type": "object", "properties": {
            "address": {"type": "string"}, "state": {"type": ["string", "null"]},
        }, "required": ["address"]},
    },
    {
        "name": "get_facility_ownership",
        "description": (
            "For one CCN: facility details, CMS owners with start dates, change-of-ownership events, "
            "and (when filing_date is given) the pipeline's timing rule: owner starts within "
            f"{ACQUISITION_WINDOW.days} days of the filing, and `cms_signal` = acquisition_financing / "
            "refinancing / null. null means CMS data can't say (it lags real sales by months), "
            "NOT that the loan is a refinancing. Always pass filing_date."
        ),
        "input_schema": {"type": "object", "properties": {
            "ccn": {"type": "string"}, "filing_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        }, "required": ["ccn"]},
    },
    {
        "name": "get_related_ucc_filings",
        "description": (
            "Other UCC filings in our database against the same (or near-identical) debtor name: "
            "lender, date, status. Shows repeat filings, a different lender later (refinancing), "
            "or a chain of filings for one operator."
        ),
        "input_schema": {"type": "object", "properties": {
            "debtor_name": {"type": "string"}, "state": {"type": ["string", "null"]},
        }, "required": ["debtor_name"]},
    },
    {
        "name": "search_deals",
        "description": (
            "Search deals already in the feed (news, CHOW, SEC, state notices) by buyer, seller, "
            "operator or facility name (case-insensitive substring). Excludes UCC-only signals "
            "unless include_ucc_signals is true. Try the distinctive core of the name."
        ),
        "input_schema": {"type": "object", "properties": {
            "name": {"type": "string"}, "state": {"type": ["string", "null"]},
            "include_ucc_signals": {"type": "boolean"},
        }, "required": ["name"]},
    },
    {
        "name": "classify_lender",
        "description": (
            "Classify a UCC secured party (real estate lender, PE sponsor, generic bank, equipment "
            "vendor, unknown) using the pipeline's lender classifier. Returns category, confidence "
            "label, the matched signal, and whether it is acquisition-relevant."
        ),
        "input_schema": {"type": "object", "properties": {
            "name": {"type": "string"}, "state": {"type": ["string", "null"]},
        }, "required": ["name"]},
    },
]


class ToolBox:
    """Owns a read-only connection. Build with ToolBox.from_config()."""

    def __init__(self, conn):
        self.conn = conn
        # Fail loudly rather than quietly holding a writable connection
        conn.set_session(readonly=True, autocommit=True)
        self._known_through: Optional[date] = None
        self._chow_rows: Optional[list] = None
        self.schemas = TOOL_SCHEMAS

    @classmethod
    def from_config(cls) -> "ToolBox":
        import psycopg2
        from config import get_config
        return cls(psycopg2.connect(get_config().database_url))

    # ── plumbing ──────────────────────────────────────────────
    def _rows(self, sql: str, params=None) -> list:
        from psycopg2.extras import RealDictCursor
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def call(self, name: str, args: dict) -> Any:
        fn = getattr(self, f"tool_{name}", None)
        if fn is None or name not in ({t["name"] for t in self.schemas} | EXTRA_TOOLS):
            raise KeyError(f"unknown tool {name!r}")
        out = fn(**(args or {}))
        # JSON-safe (dates, UUIDs, Decimals) so the trace and the model see the same thing
        return json.loads(json.dumps(out, default=str))

    # ── tools ─────────────────────────────────────────────────
    def tool_search_cms_facilities(self, name: str, state: Optional[str] = None) -> dict:
        rows = self._rows("""
            SELECT f.ccn, f.provider_name, f.legal_business_name, f.provider_city, f.provider_state,
                   f.bed_count, f.five_star_rating,
                   GREATEST(word_similarity(%(n)s, f.provider_name),
                            word_similarity(%(n)s, COALESCE(f.legal_business_name, ''))) AS score
            FROM cms_facilities f
            WHERE (%(n)s <%% f.provider_name OR %(n)s <%% f.legal_business_name
                   OR f.provider_name ILIKE %(p)s OR f.legal_business_name ILIKE %(p)s)
              AND (%(st)s::text IS NULL OR f.provider_state = %(st)s)
            ORDER BY score DESC, similarity(f.provider_name, %(n)s) DESC
            LIMIT %(lim)s
        """, {"n": name, "p": f"%{name}%", "st": state, "lim": MAX_ROWS})
        for r in rows:
            r["score"] = round(float(r["score"]), 2)
        return {"matches": rows}

    def tool_search_cms_owners(self, owner_name: str, state: Optional[str] = None) -> dict:
        rows = self._rows("""
            SELECT o.ccn, f.provider_name, f.provider_state, o.owner_name, o.owner_role, o.owner_type,
                   o.ownership_percentage, o.ownership_start_date,
                   word_similarity(%(n)s, o.owner_name) AS score
            FROM cms_ownership_records o
            JOIN cms_facilities f ON f.ccn = o.ccn
            WHERE (%(n)s <%% o.owner_name OR o.owner_name ILIKE %(p)s)
              AND (%(st)s::text IS NULL OR f.provider_state = %(st)s)
            ORDER BY score DESC, similarity(o.owner_name, %(n)s) DESC, o.ownership_start_date DESC NULLS LAST
            LIMIT %(lim)s
        """, {"n": owner_name, "p": f"%{owner_name}%", "st": state, "lim": MAX_ROWS})
        for r in rows:
            r["score"] = round(float(r["score"]), 2)
        return {"matches": rows}

    def _chow(self) -> list:
        """Rows of the newest CMS CHOW CSV, downloaded to data/chow_cache on first use."""
        if self._chow_rows is None:
            files = sorted(CHOW_CACHE.glob("SNF_CHOW_*.csv"))
            if not files:
                import requests
                from scraper.chow import CHOW_URLS, _discover_chow_csv_url
                url = _discover_chow_csv_url() or CHOW_URLS[0]
                resp = requests.get(url, timeout=120)
                resp.raise_for_status()
                CHOW_CACHE.mkdir(parents=True, exist_ok=True)
                files = [CHOW_CACHE / url.rsplit("/", 1)[-1]]
                files[0].write_bytes(resp.content)
            # CMS's file isn't valid UTF-8 (stray 0xA0 bytes)
            with files[-1].open(encoding="latin-1", newline="") as fh:
                self._chow_rows = list(csv.DictReader(fh))
        return self._chow_rows

    def tool_search_chow_records(self, name: str, state: Optional[str] = None) -> dict:
        q = _norm(name)
        if len(q) < 4:
            return {"matches": [], "note": "name too short to search"}
        hits = []
        for r in self._chow():
            for side in ("BUYER", "SELLER"):
                if state and r.get(f"ENROLLMENT STATE - {side}") != state:
                    continue
                names = [r.get(f"ORGANIZATION NAME - {side}") or "", r.get(f"DOING BUSINESS AS NAME - {side}") or ""]
                normed = [_norm(n) for n in names if n]
                if any(q in n or (len(n) >= 8 and n in q) for n in normed):
                    score = max(SequenceMatcher(None, q, n).ratio() for n in normed)
                    hits.append((score, side, r))
        hits.sort(key=lambda h: -h[0])
        return {"matches": [{
            "ccn": r["CCN - BUYER"] or r["CCN - SELLER"], "effective_date": r["EFFECTIVE DATE"],
            "chow_type": r["CHOW TYPE TEXT"], "matched_as": side.lower(),
            "buyer": r["ORGANIZATION NAME - BUYER"], "buyer_dba": r["DOING BUSINESS AS NAME - BUYER"],
            "seller": r["ORGANIZATION NAME - SELLER"], "seller_dba": r["DOING BUSINESS AS NAME - SELLER"],
            "state": r[f"ENROLLMENT STATE - {side}"], "score": round(score, 2),
        } for score, side, r in hits[:MAX_ROWS]]}

    def tool_search_cms_by_address(self, address: str, state: Optional[str] = None) -> dict:
        m = re.search(r"\b(\d+[a-z]?)\b", (address or "").lower())
        if not m:
            return {"matches": [], "note": "no street number in the address"}
        number = m.group(1)
        street = [w for w in _street_words(_NOT_ADDRESS.sub(" ", address.lower() + " ")) if w != number]
        rows = self._rows("""
            SELECT ccn, provider_name, legal_business_name, provider_address, provider_city, provider_state, bed_count
            FROM cms_facilities
            WHERE provider_address ILIKE %(p)s AND (%(st)s::text IS NULL OR provider_state = %(st)s)
        """, {"p": f"{number} %", "st": state})
        for r in rows:
            words = set(_street_words(r["provider_address"]))
            r["street_words_matched"] = [w for w in street if w in words]
        rows = [r for r in rows if r["street_words_matched"] or not street]
        rows.sort(key=lambda r: -len(r["street_words_matched"]))
        return {"matches": rows[:MAX_ROWS], "street_words_searched": street}

    def _direct_owner_rows(self, ccn: Optional[str] = None) -> list:
        """(ccn, owner, start, is_direct) rows, same filter as scripts/export_deals.py."""
        sql = """
            SELECT DISTINCT ccn, owner_name, ownership_start_date, owner_role NOT ILIKE %(ind)s AS is_direct
            FROM cms_ownership_records
            WHERE ownership_start_date IS NOT NULL
              AND owner_role ILIKE %(dir)s
              AND (owner_role NOT ILIKE %(ind)s OR owner_type ILIKE 'ORG%%')
        """
        params = {"ind": "%INDIRECT%", "dir": "%DIRECT OWNERSHIP%"}
        if ccn:
            sql += " AND ccn = %(ccn)s"
            params["ccn"] = ccn
        return self._rows(sql, params)

    def _cms_complete_through(self) -> date:
        """How far CMS owner data is complete (cached; one full-table scan)."""
        if self._known_through is None:
            rows = self._direct_owner_rows()
            self._known_through = complete_through(
                owner_start_dates((r["ccn"], r["owner_name"], r["ownership_start_date"])
                                  for r in rows if r["is_direct"]))
        return self._known_through

    def tool_get_facility_ownership(self, ccn: str, filing_date: Optional[str] = None) -> dict:
        fac = self._rows("""
            SELECT ccn, provider_name, legal_business_name, provider_city, provider_state, bed_count,
                   five_star_rating, sff_flag
            FROM cms_facilities WHERE ccn = %s
        """, (ccn,))
        if not fac:
            return {"facility": None, "note": f"no CMS facility with CCN {ccn}"}
        owners = self._rows("""
            SELECT owner_name, owner_type, owner_role, ownership_percentage, ownership_start_date
            FROM cms_ownership_records WHERE ccn = %s
            ORDER BY ownership_start_date DESC NULLS LAST LIMIT 25
        """, (ccn,))
        chow = []
        for r in self._rows("SELECT buyer_name, effective_date FROM chow_seen_records WHERE LEFT(ccn, 6) = %s", (ccn[:6],)):
            d = _mdy(r["effective_date"])
            if d:
                chow.append({"buyer_name": r["buyer_name"], "effective_date": d.isoformat()})
        out = {"facility": fac[0], "owners": owners, "chow_events": chow}

        filed = _day(filing_date)
        if filed:
            rows = self._direct_owner_rows(ccn)
            starts = owner_start_dates((r["ccn"], r["owner_name"], r["ownership_start_date"]) for r in rows)
            starts.setdefault(ccn, set()).update(_mdy_or_none(c["effective_date"]) for c in chow)
            starts[ccn].discard(None)
            known_through = self._cms_complete_through()
            near = sorted(
                ({"date": s.isoformat(), "days_from_filing": (s - filed).days}
                 for s in starts.get(ccn, ()) if abs(s - filed) <= ACQUISITION_WINDOW),
                key=lambda x: x["date"])
            out["timing"] = {
                "filing_date": filed.isoformat(),
                "window_days": ACQUISITION_WINDOW.days,
                "owner_starts_near_filing": near,
                "cms_signal": _cms_signal(filed, [ccn], starts, known_through),
                "cms_owner_data_complete_through": known_through.isoformat(),
            }
        return out

    def tool_get_related_ucc_filings(self, debtor_name: str, state: Optional[str] = None) -> dict:
        rows = self._rows("""
            SELECT state, filing_number, debtor_name, secured_party, filing_date, filing_type, status,
                   confidence AS lender_label, detail_url
            FROM ucc_filings
            WHERE similarity(debtor_name, %(n)s) > 0.6
              AND (%(st)s::text IS NULL OR state = %(st)s)
            ORDER BY filing_date DESC NULLS LAST
            LIMIT %(lim)s
        """, {"n": debtor_name, "st": state, "lim": MAX_ROWS})
        return {"filings": rows}

    def tool_search_deals(self, name: str, state: Optional[str] = None, include_ucc_signals: bool = False) -> dict:
        rows = self._rows("""
            SELECT d.id, d.acquiring_entity, d.seller_entity, d.operator_names, d.facility_names, d.states,
                   d.acquisition_date, d.lender, d.stage, d.extraction_model
            FROM deals d
            WHERE (d.acquiring_entity ILIKE %(p)s OR d.seller_entity ILIKE %(p)s
                   OR EXISTS (SELECT 1 FROM unnest(d.operator_names) x WHERE x ILIKE %(p)s)
                   OR EXISTS (SELECT 1 FROM unnest(d.facility_names) x WHERE x ILIKE %(p)s))
              AND (%(st)s::text IS NULL OR %(st)s = ANY(d.states))
              AND (%(ucc)s OR d.extraction_model IS DISTINCT FROM 'ucc_filing')
              AND d.stage <> 'dismissed'
            ORDER BY d.acquisition_date DESC NULLS LAST
            LIMIT %(lim)s
        """, {"p": f"%{name}%", "st": state, "ucc": bool(include_ucc_signals), "lim": MAX_ROWS})
        return {"deals": rows}

    def tool_get_ucc_filing(self, state: str, filing_number: str) -> dict:
        rows = self._rows("""
            SELECT state, filing_number, debtor_name, secured_party, filing_date, filing_type, status,
                   collateral_description, confidence AS lender_label, detail_url
            FROM ucc_filings WHERE state = %s AND filing_number = %s
        """, (state, filing_number))
        return {"filing": rows[0] if rows else None}

    def tool_classify_lender(self, name: str, state: Optional[str] = None) -> dict:
        c = classify_secured_party(name, state=state)
        return {
            "secured_party": name,
            "category": c.category.value,
            "label": to_confidence_label(c),
            "confidence": c.confidence,
            "matched_signal": c.matched_signal,
            "is_acquisition_relevant": c.is_acquisition_relevant,
            # The site's own rules (pipeline/deal_labels.py), which the classifier above doesn't know
            "nursing_home_bank": bool(_HOME_BANK_RE.search(name or "")),
            "small_business_or_filing_agent": bool(SMALL_BUSINESS_LENDER_RE.search(name or "")),
        }


def _mdy_or_none(iso: str) -> Optional[date]:
    return _day(iso)
