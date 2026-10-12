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
            cur.execute("SELECT ccn, provider_name, provider_state, bed_count FROM cms_facilities")
            for ccn, name, state, beds in cur.fetchall():
                self.facility[ccn] = (name, state, beds)
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
            cur.execute("SELECT ccn, buyer_name, effective_date FROM chow_seen_records")
            for ccn, buyer, eff in cur.fetchall():
                if _mdy(eff):
                    self.events[ccn].append((_mdy(eff), buyer, "CMS change-of-ownership file"))

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
