"""
What CMS data says about a deal's facilities: which CMS facilities it is
(CCNs), how many beds they have, and whether CMS shows a new owner around
the deal date. Read when deals.json is written, so a pending deal turns
Confirmed by itself once the monthly CMS ownership file (or the quarterly
CHOW file) catches up -- Selectis' GA sale, for one, isn't in CMS yet.

CCNs come from the pipeline's CMS matcher (cms_matches). For state filings
and news without one, a facility name that matches exactly one CMS facility
in the same state (after normalizing spelling) adds it; nothing fuzzy.
"""

import re
from collections import defaultdict
from datetime import date, timedelta

WINDOW = timedelta(days=183)


def facility_key(name: str) -> str:
    n = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    n = re.sub(r"\b(THE|LLC|INC|L L C|AND)\b", " ", n)
    n = re.sub(r"\bCTR\b", "CENTER", n)
    n = re.sub(r"\bREHAB\b", "REHABILITATION", n)
    n = re.sub(r"\bNSG\b", "NURSING", n)
    n = re.sub(r"\bHEALTH CARE\b", "HEALTHCARE", n)
    return " ".join(n.split())


CMS_NAME_LIMIT = 50  # cms_facilities.provider_name is cut off at 50 characters

# Words every nursing home name shares. What's left must match exactly for a
# fuzzy name match to count: "The Ridge Post Acute" -> {RIDGE} is not "Ocean
# Ridge Post Acute" -> {OCEAN, RIDGE}, but "Westwood Post Acute" is
# "Westwood Post Acute Care" (2026-10-12 audit: half of the non-identical
# fuzzy matches on UCC records were a different home).
_GENERIC_WORDS = set("""
THE OF AT AND ON IN FOR A AN LLC INC L L C LP CO CORP CORPORATION COMPANY OPERATING OPERATIONS OPCO PROPCO
NURSING REHABILITATION REHAB HEALTH HEALTHCARE CARE CENTER CENTRE CTR HOME HOMES FACILITY SKILLED UNIT VILLAGE
POST ACUTE CONVALESCENT HOSPITAL WELLNESS LIVING SENIOR COMMUNITY MANOR RESIDENCE SUBACUTE EXTENDED
LONG TERM TRANSITIONAL CARE SERVICES CENTERS SNF
""".split())


def distinctive_key(name: str) -> frozenset:
    words = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper()).split()
    return frozenset(w for w in words if w not in _GENERIC_WORDS)


def same_facility_name(a: str, b: str) -> bool:
    """Two facility names agree once generic words are set aside. A name CMS
    cut off at 50 characters may drop the last word."""
    # A cut-off last word ("...REHABILITATION AND NURSI") is a fragment of a
    # word in the full name; drop it
    words_a = re.sub(r"[^A-Z0-9 ]", " ", (a or "").upper()).split()
    words_b = re.sub(r"[^A-Z0-9 ]", " ", (b or "").upper()).split()
    if words_b:
        full = [w for w in words_a if w != words_b[-1] and w.startswith(words_b[-1])]
        if full:
            words_b = words_b[:-1]
            words_a = [w for w in words_a if w != full[0]]
        elif len(words_b[-1]) >= 3 and any(g != words_b[-1] and g.startswith(words_b[-1]) for g in _GENERIC_WORDS):
            words_b = words_b[:-1]  # "...SKILLED CARE AND REHABILIT"
    ka, kb = distinctive_key(" ".join(words_a)), distinctive_key(" ".join(words_b))
    return bool(ka) and ka == kb


def facility_names_agree(a: str, b: str) -> bool:
    """same_facility_name, either way round, or the same name once
    punctuation and case are set aside ("Vista Grande Villa", all generic
    words), or one name cut off from the other ("Optalis Health And
    Rehabilitation Of Three" / "...of Three Rivers")."""
    na = " ".join(re.sub(r"[^A-Z0-9 ]", " ", (a or "").upper()).split())
    nb = " ".join(re.sub(r"[^A-Z0-9 ]", " ", (b or "").upper()).split())
    if not na or not nb:
        return False
    short, long_ = sorted((na, nb), key=len)
    return (same_facility_name(a, b) or same_facility_name(b, a) or na == nb
            or (len(short) >= 12 and long_.startswith(short)))


def legal_key(name: str) -> str:
    """facility_key without the words that tell an operating company from its home."""
    k = re.sub(r"\b(OPCO|OPERATIONS|OPERATING|OPERATOR|CO|CORP|INCORPORATED|LIMITED|LP|PLLC)\b", " ",
               facility_key(name))
    return " ".join(k.split())


def _street_in(address: str | None, text: str) -> bool:
    m = re.match(r"(\d+)\s+([A-Za-z]+)", address or "")
    return bool(m and text and re.search(rf"\b{m[1]}\s+{re.escape(m[2])}\b", text, re.I))


def _mdy(s):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})$", s or "")
    return date(int(m[3]), int(m[1]), int(m[2])) if m else None


class CmsIndex:
    def __init__(self, conn):
        self.facility = {}                 # ccn -> (name, state, beds)
        self.by_name = defaultdict(set)    # (state, key) -> {ccn}
        self.truncated = defaultdict(list) # state -> [(key prefix, ccn)] for cut-off names
        self.events = defaultdict(list)    # ccn -> [(date, owner, source)]
        with conn.cursor() as cur:
            self.licensee = {}             # ccn -> legal business name (the licensed company)
            self.address = {}              # ccn -> street address
            cur.execute("SELECT ccn, provider_name, provider_state, bed_count, legal_business_name, provider_address "
                        "FROM cms_facilities")
            for ccn, name, state, beds, legal, address in cur.fetchall():
                self.facility[ccn] = (name, state, beds)
                if legal:
                    self.licensee[ccn] = legal
                if address:
                    self.address[ccn] = address
                self.by_name[(state, facility_key(name))].add(ccn)
                if len(name or "") >= CMS_NAME_LIMIT:
                    # drop the cut-off last word: "...Rehabilitation Ce"
                    self.truncated[state].append((facility_key(name).rsplit(" ", 1)[0], ccn))
            cur.execute("""
                SELECT DISTINCT ccn, ownership_start_date, owner_name FROM cms_ownership_records
                WHERE ownership_start_date IS NOT NULL
                  AND owner_role ILIKE '%DIRECT OWNERSHIP%' AND owner_role NOT ILIKE '%INDIRECT%'
            """)
            for ccn, start, owner in cur.fetchall():
                self.events[ccn].append((start, owner, "CMS ownership data"))
            cur.execute("SELECT LEFT(ccn, 6), buyer_name, effective_date FROM chow_seen_records")  # repeat-CHOW suffix off
            for ccn, buyer, eff in cur.fetchall():
                if _mdy(eff):
                    self.events[ccn].append((_mdy(eff), buyer, "CMS change-of-ownership file"))
        # Buyer and seller of each sale, from the CHOW file itself:
        # chow_seen_records keeps only the buyer
        from scraper.chow import load_chow_rows
        self.sales = defaultdict(list)     # ccn -> [(date, buyer, seller)]
        for r in load_chow_rows():
            eff, buyer = _mdy((r.get("EFFECTIVE DATE") or "").strip()), (r.get("ORGANIZATION NAME - BUYER") or "").strip()
            ccn = (r.get("CCN - BUYER") or r.get("CCN - SELLER") or "").strip()[:6]
            if eff and buyer and ccn:
                self.sales[ccn].append((eff, buyer, (r.get("ORGANIZATION NAME - SELLER") or "").strip() or None))

    def ccns_by_name(self, facility_names, states) -> list[str]:
        """CCNs whose CMS name equals one of the facility names, when that
        name is unique in the state."""
        out = []
        for f in facility_names or []:
            for st in states or []:
                key = facility_key(f)
                hit = self.by_name.get((st, key)) or {c for prefix, c in self.truncated.get(st, ())
                                                       if key.startswith(prefix + " ")}
                if hit and len(hit) == 1:
                    out += [c for c in hit if c not in out]
        return out

    def ccns_for_notice(self, facility_names, states, text: str) -> tuple[list[str], str | None]:
        """For a state notice no exact name matched: a home the buyer has
        renamed, or a notice that gives only the address. Only signals that
        were right on every one of the 68 unlinked notices checked 2026-10-28:
          address    - the notice's text has the CMS street number and first street
                       word ("600 W Cheltenham" -> Independence Rehab and Nursing)
          legal_name - a name in the notice is the home's CMS legal business name
                       ("Arabella Health & Wellness of Butler" -> Timberland, whose
                       licensee is "Arabella Health & Wellness of Butler Opco LLC")
          name       - the same two or more distinctive words as the CMS name
                       ("Cypress Cove Post Acute" -> "Cypress Cove Care Center")
        Each must point to exactly one home in the notice's states. One shared
        word is not enough: "Heritage Village Nursing Home" is not Heritage Manor."""
        states = set(states or [])
        in_states = [c for c, (_, st, _) in self.facility.items() if st in states]
        tests = [
            ("address", lambda c, n: _street_in(getattr(self, "address", {}).get(c), text)),
            ("legal_name", lambda c, n: bool(getattr(self, "licensee", {}).get(c)) and
                legal_key(n) == legal_key(self.licensee[c])),
            ("name", lambda c, n: len(distinctive_key(n)) >= 2 and (
                distinctive_key(n) == distinctive_key(self.facility[c][0])
                # a cut-off last word only where CMS really cut the name off:
                # "Atrium ... of Wayneview" is not "ATRIUM ... OF WAYNE" (another home)
                or (len(self.facility[c][0] or "") >= CMS_NAME_LIMIT and same_facility_name(n, self.facility[c][0])))),
        ]
        for basis, test in tests:
            hits = set()
            for n in facility_names or [None]:
                if n is None and basis != "address":
                    continue
                found = {c for c in in_states if test(c, n)}
                if len(found) == 1:
                    hits |= found
            if hits:
                return sorted(hits), basis
        return [], None

    def sale(self, ccns, when: date | None) -> dict | None:
        """The CHOW-file sale at one of these facilities nearest the date,
        within 6 months either way: who bought it and who sold it."""
        if not when:
            return None
        best = None
        for c in ccns or []:
            for eff, buyer, seller in getattr(self, "sales", {}).get(c, ()):
                gap = abs(eff - when)
                if gap <= WINDOW and (best is None or gap < best[0]):
                    best = (gap, {"date": eff.isoformat(), "buyer": buyer, "seller": seller, "ccn": c,
                                  "source": "CMS change-of-ownership file",
                                  "facility": self.facility.get(c, (None,))[0]})
        return best[1] if best else None

    def current_licensee(self, ccns) -> str | None:
        """The one licensed company CMS lists today for these facilities."""
        names = {getattr(self, "licensee", {}).get(c) for c in ccns or []} - {None}
        return names.pop() if len(names) == 1 else None

    def beds(self, ccns, facility_count: int | None = None) -> int | None:
        """Total CMS certified beds, only when every facility in the record
        has one: 189 beds for PACS' 34-facility deal would be the few homes
        that happened to match, not the deal."""
        counts = [self.facility[c][2] for c in set(ccns or []) if c in self.facility and self.facility[c][2]]
        if not counts or (facility_count and len(counts) < facility_count):
            return None
        return sum(counts)

    def new_owner(self, ccns, when: date | None) -> dict | None:
        """The CMS ownership change at one of these facilities nearest the
        deal date, within 6 months either way. `owner` is set only when CMS
        names a single new owner that day: several new direct owners at once
        (family trusts in an Oklahoma stock transfer) don't make one of them
        "the buyer"."""
        if not when:
            return None
        best = None
        for c in ccns or []:
            for start, owner, source in self.events.get(c, ()):
                gap = abs(start - when)
                if gap <= WINDOW and (best is None or gap < best[0]):
                    best = (gap, c, start, source)
        if not best:
            return None
        _, c, start, source = best
        owners = sorted({o for s, o, src in self.events[c] if s == start and src == source and o})
        return {"date": start.isoformat(), "owner": owners[0] if len(owners) == 1 else None,
                "owner_count": len(owners), "ccn": c, "source": source,
                "facility": self.facility.get(c, (None,))[0]}
