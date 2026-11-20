"""
One card per transaction: group exported records that report the same deal.

pipeline/same_sale.py merges duplicates in the database, but only when two
rows share a facility; news rarely names one, so the same sale still showed
as several cards (Genesis from McKnight's plus the Maine and North Carolina
filings; PACS-Eduro from two stories). This groups them for display only,
when deals.json is written. Nothing is merged or hidden in the database, and
every record keeps its own permalink.

UCC filings: one loan is often filed twice on the same day, against the home's
property company and its operating company (2026-11-16 judge audit: ~370
pairs, a tenth of UCC cards). Two UCC records are grouped when one's CMS
facilities include the other's (a portfolio loan is filed against the holding
company or master tenant for every home, and against each home: 2026-11-20
scan, ~31 such pairs), they name the same lender (boilerplate such as "as
agent" ignored) and were filed within UCC_SAME_LOAN_DAYS of each other.
Amendments and vendor liens are left alone. The card is the filing with the
most specific type (acquisition financing, then refinancing, then plain
financing; the propco side often lacks the CMS link that tells them apart)
and lists both borrowers.

Records of the same deal type are grouped when one of these holds, within
6 months of each other:
  - they share a CMS facility (CCN) and their buyers agree (or one names none);
  - both name the buyer and the seller, both agree, and their facility
    counts don't differ (when both give one);
  - both name the buyer, list the same states, and give the same price.
Names agree only when identical once case, punctuation, legal suffixes
and "Group"/"Holdings"/"Company" are ignored. Two different named buyers stay apart: an opco and a propco bought
separately are two transactions -- shown as linked cards (`related`), not one
(person's decision 2026-11-20).

Added 2026-11-20 after a scan of the live site found ~55 uncaught duplicate
pairs (person chose all four fixes):
  - a state filing and the CMS record of the same home within 90 days, or two
    state records of one home within a week, whatever buyer each names (the
    state names the parent, CMS the operating LLC: Golden Madera, Mayflower);
  - a news story and a state filing in the same state naming the same home
    within 120 days, or with the same buyer brand ("Advinia Care" /
    "AdviniaCare at Warren, LLC"), states and home count within 45 days;
  - two stories of one deal with near-equal numbers: the same seller and a
    price within 1%; the same buyer, states and date with home counts one
    apart; or the same seller and home count with a buyer named by one at most.

The group's card is its most advanced record (Confirmed, then Reported, then
Expected; ties go to the most complete). It lists every record's source, plus
rows same_sale.py merged into any of them (deals.merged_into).
"""

from datetime import date

import re

from pipeline.deal_labels import after_closing
from pipeline.dedup import _completeness_score, normalize_acquirer

WINDOW_DAYS = 183
# Words that don't tell two companies apart ("Saber Healthcare Group" is
# "Saber Healthcare"); anything else must match exactly -- fuzzy scores put
# "North Mobile SNF Property Holdings" at 90 against "Mobile SNF Property
# Holdings", a different propco
_GENERIC = re.compile(r"\b(GROUP|COMPANY|CO|HOLDINGS?|THE)\b")
STATUS_RANK = {"confirmed": 4, "on_file": 4, "reported": 3, "likely_closed": 2.5, "expected": 2, "unconfirmed": 1}
GROUPED_TYPES = ("ownership_change", "lease", "control_change")
UCC_LOAN_TYPES = {"acquisition_financing": 3, "refinancing": 2, "financing": 1}
UCC_SAME_LOAN_DAYS = 30
RELATED_DAYS = 183   # = WINDOW_DAYS: propco and opco filings can be months apart (4401 Haverford, PA)
LICENSEE_NEWS_DAYS = 90
STATE_CMS_DAYS = 90
SAME_STORY_DAYS = 45


def lender_key(lender: str | None) -> str:
    """A UCC lender less filing boilerplate; '' when the lender is unknown."""
    s = (lender or "").upper()
    if not s or s.startswith("NOT AVAILABLE"):
        return ""
    s = s.split(";")[0]
    s = re.sub(r",?\s+(ITS SUCCESSORS\b.*|AS (ADMINISTRATIVE |COLLATERAL )?AGENT\b.*|F/?K/?A\b.*)$", "", s)
    return name_key(s)


# A home's name without the words every home shares, and without the
# "X Operations LLC DBA" prefix a licence list puts before it (as evals/judge.py)
_HOME_WORDS = re.compile(r"\b(and|of|the|at|post acute|rehabilitation|rehab|nursing|center|centre|"
                         r"healthcare|health|care|skilled|snf|dba|operations|llc|inc)\b")


def _norm(v) -> str:
    s = re.sub(r"\b(llc|inc|corp|co|lp|ltd|the|company|holdings?)\b", " ", str(v or "").lower())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def home_key(name) -> str:
    s = _norm(name).split(" dba ", 1)[-1]
    s = " ".join(_HOME_WORDS.sub(" ", s).split())
    return s if len(s) >= 5 else ""


def brand(buyer) -> str:
    """The name squeezed together ("Advinia Care" -> "adviniacare")."""
    s = _norm(buyer).replace(" ", "")
    return s if len(s) >= 6 else ""


def brands_agree(a, b) -> bool:
    """One name starts with the other ("adviniacare" / "adviniacareatwarren")."""
    x, y = brand(a), brand(b)
    return bool(x and y and (x.startswith(y) or y.startswith(x)))


def _homes(d) -> set:
    return {home_key(n) for n in d.get("facility_names") or []} - {""}


def _homes_meet(a: dict, b: dict) -> bool:
    """The cards name a home in common; stored names can be cut short
    ("greenville skille"), so one name starting with the other counts."""
    for x in _homes(a):
        for y in _homes(b):
            if x == y or (min(len(x), len(y)) >= 8 and (x.startswith(y) or y.startswith(x))):
                return True
    return False


def ucc_same_loan(a: dict, b: dict) -> bool:
    """Two UCC filings that are one loan (property + operating company, or a
    portfolio loan's holding-company filing + one home's)."""
    if a.get("source_type") != "ucc" or b.get("source_type") != "ucc":
        return False
    if a.get("deal_type") not in UCC_LOAN_TYPES or b.get("deal_type") not in UCC_LOAN_TYPES:
        return False
    ca, cb = set(a.get("ccns") or []), set(b.get("ccns") or [])
    if not ca or not cb or not (ca <= cb or cb <= ca):
        return False
    # a filing against a person lists the homes it might be on: only an
    # identical list (and the old 3-day window) means one loan
    if (a.get("individual_filing") or b.get("individual_filing")) and (
            ca != cb or abs(((_day(a) or date.min) - (_day(b) or date.min)).days) > 3):
        return False
    la = lender_key(a.get("lender"))
    if not la or la != lender_key(b.get("lender")):
        return False
    da, db = _day(a), _day(b)
    return bool(da and db and abs((da - db).days) <= UCC_SAME_LOAN_DAYS)


def _group_ucc(deals: list[dict], merged: dict) -> int:
    """Group same-loan UCC filings (see module doc). Returns groups made."""
    rows = [d for d in deals if d.get("source_type") == "ucc" and d.get("deal_type") in UCC_LOAN_TYPES
            and d.get("ccns") and not d.get("grouped_into") and lender_key(d.get("lender"))]
    parent = {d["id"]: d["id"] for d in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_lender = {}
    for d in rows:
        by_lender.setdefault(lender_key(d.get("lender")), []).append(d)
    for members in by_lender.values():
        members.sort(key=lambda d: _day(d) or date.max)
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                da, db = _day(a), _day(b)
                if not da or not db or (db - da).days > UCC_SAME_LOAN_DAYS:
                    break
                if ucc_same_loan(a, b):
                    parent[find(a["id"])] = find(b["id"])
    runs = {}
    for d in rows:
        runs.setdefault(find(d["id"]), []).append(d)
    n = 0
    for run in runs.values():
        if len(run) < 2:
            continue
        # the filing covering the most homes leads (a portfolio loan's
        # holding company), then the most specific loan type
        lead = max(run, key=lambda d: (len(set(d["ccns"])), UCC_LOAN_TYPES[d["deal_type"]],
                                       _completeness_score(d), d.get("created_at") or ""))
        run.sort(key=lambda d: (d is not lead, _day(d) or date.max))
        sources = [_source_entry(d) for d in run]
        for d in run:
            sources += [{**m, "merged": True} for m in merged.get(d["id"], [])]
            if d is not lead:
                d["grouped_into"] = lead["id"]
        debtors = list(dict.fromkeys(d["ucc_debtor"] for d in run if d.get("ucc_debtor")))
        lead.update({"sources": sources, "group_size": len(run),
                     "group_ccns": sorted({c for d in run for c in d["ccns"]}),
                     "ucc_debtor": "; ".join(debtors) or lead.get("ucc_debtor")})
        n += 1
    return n


def _day(d):
    s = d.get("acquisition_date") or (d.get("created_at") or "")[:10]
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def name_key(name: str) -> str:
    n = re.sub(r"[^A-Z0-9 ]", " ", normalize_acquirer(name))
    return " ".join(_GENERIC.sub(" ", n).split())


def _agree(a, b) -> bool:
    return name_key(a) == name_key(b)


def same_deal(a: dict, b: dict) -> str | None:
    """Why two records are the same deal, or None."""
    if a["deal_type"] != b["deal_type"] or a["deal_type"] not in GROUPED_TYPES:
        return None
    da, db = _day(a), _day(b)
    if not da or not db or abs((da - db).days) > WINDOW_DAYS:
        return None
    ba, bb = a.get("buyer"), b.get("buyer")
    shared = set(a.get("ccns") or []) & set(b.get("ccns") or [])
    # A state record made after a sale CMS's CHOW file has: the state names a
    # holding company, CMS the operator (Iris Cove, TN, 2026-10-27)
    ratifies = shared and {a.get("source_type"), b.get("source_type")} == {"chow", "con"} and (
        after_closing(a) or after_closing(b))
    # A state filing names the new licensee, the news story its parent
    # (Crystal Lake, RI: Pascoag Crystal Lake Rehab Center LLC / Health
    # Concepts, gold set 2026-11-16). One home rarely sells twice in 90 days
    kinds = {a.get("source_type"), b.get("source_type")}
    gap = abs((da - db).days)
    licensee_parent = bool(shared) and "con" in kinds and bool(kinds & {"rss", "edgar"}) and (
        gap <= LICENSEE_NEWS_DAYS)
    # One card's seller is the other's buyer: the next sale of the home, not
    # this one (AL: Generations -> SouthCare in Nov, SouthCare -> Arabella in Jan)
    # (full names only: "Erwin Health Care Associates" sold to "Erwin Healthcare LLC")
    if any(x.get("seller") and y.get("buyer") and _agree(x["seller"], y["buyer"])
           for x, y in ((a, b), (b, a))):
        return None
    # The state's filing and CMS's record of one home name different
    # companies (parent / operating LLC); one home rarely sells twice in 90
    # days (Golden Madera, CA; Mayflower's RI homes; 2026-11-20 scan)
    state_and_cms = bool(shared) and kinds == {"con", "chow"} and gap <= STATE_CMS_DAYS
    # One state notice stored as a combined and a per-home record (RI Sunny View)
    one_notice = bool(shared) and kinds == {"con"} and (
        gap <= 7 or (a.get("published_at") and a.get("published_at") == b.get("published_at")))
    if shared and (state_and_cms or one_notice):
        return "same CMS facility"
    news_kinds = kinds & {"rss", "edgar"}
    # A news story and a state filing about the same home: the story names the
    # parent and often no CMS link (Ensign's 8 FL homes / Bay Park; Advinia's
    # 3 RI homes / Warren, Greenville, Coventry)
    if (news_kinds and "con" in kinds and len(kinds) == 2 and not (a.get("ccns") and b.get("ccns"))
            and set(a.get("states") or []) & set(b.get("states") or [])):
        ca, cb = a.get("facility_count"), b.get("facility_count")
        if _homes_meet(a, b) and gap <= LICENSEE_NEWS_DAYS + 30:
            return "news and state filing name the same home"
        news, filing = (a, b) if a.get("source_type") in ("rss", "edgar") else (b, a)
        # a story about several homes and one of their licensee filings
        # ("Advinia Care", 3 RI homes / "AdviniaCare at Greenville, LLC")
        if (brands_agree(ba, bb) and (news.get("facility_count") or 0) >= max(filing.get("facility_count") or 1, 2)
                and set(filing.get("states") or []) <= set(news.get("states") or [])
                and gap <= LICENSEE_NEWS_DAYS + 30):
            return "same buyer brand, a home of the story"
        if (brands_agree(ba, bb) and ca and ca == cb and gap <= SAME_STORY_DAYS
                and sorted(a.get("states") or []) == sorted(b.get("states") or [])):
            return "same buyer, states and home count"
    if kinds <= {"rss", "edgar"} and gap <= SAME_STORY_DAYS:
        why = _same_story(a, b)
        if why:
            return why
    if ba and bb and not _agree(ba, bb) and not ratifies and not licensee_parent:
        return None
    if shared:
        return "same CMS facility"
    # Different homes are different deals, whatever else matches (a hospital
    # district taking over two homes a month apart)
    if a.get("ccns") and b.get("ccns"):
        return None
    news = {a.get("source_type"), b.get("source_type")} <= {"rss", "edgar"}
    same_states = bool(a.get("states")) and sorted(a["states"]) == sorted(b.get("states") or [])
    ca, cb = a.get("facility_count"), b.get("facility_count")
    same_count = ca is not None and ca == cb
    # Different prices are different sales: Welltower's 3 Miami-area homes
    # for $143M and its 3 Broward homes for $160M, a week apart
    va, vb = a.get("deal_value_m"), b.get("deal_value_m")
    price_differs = va is not None and vb is not None and abs(float(va) - float(vb)) > 0.05 * max(float(va), float(vb))
    # Two stories of one sale with no seller or price: Ensign's 4 WA homes
    # on 10/01 and 10/02, its 7 CO operations (one copy undated)
    if (news and ba and bb and same_states and same_count and not price_differs
            and abs((da - db).days) <= SAME_STORY_DAYS):
        return "same buyer, states and home count"
    # One names the seller, the other the broker's buyerless summary: same
    # closing date, state and count (Saber's 3 Ohio homes, closed 6/30)
    sa, sb = a.get("seller"), b.get("seller")
    if (news and same_states and same_count and (ca >= 2 or (sa and sb and _agree(sa, sb)))
            and a.get("acquisition_date") and a.get("acquisition_date") == b.get("acquisition_date")
            and not (sa and sb and not _agree(sa, sb)) and not price_differs):
        return "same closing date, states and home count"
    if not (ba and bb):
        return None
    if sa and sb:
        # NHC buying 5 homes in June and 35 in July from NHI are two deals
        ca, cb = a.get("facility_count"), b.get("facility_count")
        if ca and cb and ca != cb:
            return None
        return "same buyer and seller" if _agree(sa, sb) else None
    if (a.get("states") and sorted(a["states"]) == sorted(b.get("states") or [])
            and a.get("deal_value_m") is not None and a.get("deal_value_m") == b.get("deal_value_m")):
        return "same buyer, states and price"
    return None


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _same_story(a: dict, b: dict) -> str | None:
    """Two news stories of one deal whose numbers differ a little (2026-11-20
    scan): Genesis $1,000M / $996M; Ensign's FL homes 9 / 8 on one date;
    LTC's 17 homes, one story naming no buyer."""
    sa, sb = a.get("seller"), b.get("seller")
    ba, bb = a.get("buyer"), b.get("buyer")
    sellers = bool(sa and sb and (_agree(sa, sb) or brands_agree(sa, sb)))
    states = sorted(a.get("states") or []) == sorted(b.get("states") or [])
    va, vb = _num(a.get("deal_value_m")), _num(b.get("deal_value_m"))
    priced = va is not None and vb is not None
    if priced and abs(va - vb) > 0.01 * max(va, vb):
        return None
    if sellers and priced and states:
        return "same seller and price"
    # "BH Group and Gold Standard of Care" / "... joint venture", $10M, FL
    if ba and bb and brands_agree(ba, bb) and priced and states and a.get("states"):
        return "same buyer, states and price"
    ca, cb = a.get("facility_count"), b.get("facility_count")
    # a story naming neither party, same state, home count and exact price
    # ($10.5M Ohio sale / Green Palms of Shelby, 28 days apart; person's label)
    if (priced and va == vb and va >= 1 and states and a.get("states") and ca and ca == cb
            and ((not ba and not sa) or (not bb and not sb))):
        return "same states, home count and price; one story names no parties"
    if (ba and bb and (_agree(ba, bb) or brands_agree(ba, bb)) and states and a.get("states")
            and a.get("acquisition_date") and a.get("acquisition_date") == b.get("acquisition_date")
            and ca and cb and min(ca, cb) >= 5 and abs(ca - cb) <= 1):
        return "same buyer, states and date, home count one apart"
    if sellers and states and ca and ca == cb and ca >= 2 and not (ba and bb):
        return "same seller and home count"
    return None


def _source_entry(d: dict) -> dict:
    return {"id": d["id"], "source_type": d.get("source_type"), "source_name": d.get("source_name"),
            "source_url": d.get("source_url"), "source_title": d.get("source_title"),
            "date": d.get("acquisition_date") or (d.get("created_at") or "")[:10],
            "found_date": (d.get("created_at") or "")[:10], "status": d.get("status"),
            "headline": "".join(t for t, _ in d.get("headline") or [])}


def _link_related(deals: list[dict], find) -> None:
    """Cards on the same home within RELATED_DAYS that aren't one deal: the
    buildings sold to one company and the operations to another (Standard
    Bearer + Ensign, 2 TX homes). Each lists the other in `related` (person's
    decision 2026-11-20: two cards, linked)."""
    shown = [d for d in deals if d.get("deal_type") in GROUPED_TYPES and not d.get("grouped_into")]
    homes = {d["id"]: set(_homes(d)) for d in shown}
    for d in deals:   # a group's card carries the homes of every record behind it
        if d.get("grouped_into") in homes:
            homes[d["grouped_into"]] |= _homes(d)
    keys = {}
    for d in shown:
        for c in d.get("group_ccns") or d.get("ccns") or []:
            keys.setdefault(f"ccn:{c}", []).append(d)
        for st in d.get("states") or []:
            for h in homes[d["id"]]:
                keys.setdefault(f"home:{st}:{h}", []).append(d)
        # two stories, same states, home count and date, different buyers: the
        # buildings and the operations (LTC + Vierra, MD/VA; Standard Bearer + Ensign, TX)
        if d.get("source_type") in ("rss", "edgar") and (d.get("facility_count") or 0) >= 2 and d.get("states") and d.get("buyer"):
            keys.setdefault(f"story:{sorted(d['states'])}:{d['facility_count']}", []).append(d)
    for key, rows in keys.items():
        limit = 3 if key.startswith("story:") else RELATED_DAYS
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                if a is b or find(a["id"]) == find(b["id"]):
                    continue
                if key.startswith("story:") and (_agree(a["buyer"], b["buyer"]) or brands_agree(a["buyer"], b["buyer"])):
                    continue   # one buyer's two deals, not buildings + operations
                da, db = _day(a), _day(b)
                if not da or not db or abs((da - db).days) > limit:
                    continue
                for x, y in ((a, b), (b, a)):
                    rel = x.setdefault("related", [])
                    if y["id"] not in {r["id"] for r in rel}:
                        rel.append({"id": y["id"], "headline": "".join(t for t, _ in y.get("headline") or []),
                                    "date": y.get("acquisition_date"), "source_type": y.get("source_type")})


# Licence batches (several homes moving to new same-pattern LLCs on one day,
# e.g. 30 Missouri "... SNF OPCO LLC") were one card from 2026-10-27 to
# 11-17. Each home's new licensee is its own company, so each is its own card
# (user decision 2026-11-17).


def group(deals: list[dict], merged: dict[str, list[dict]]) -> dict:
    """Set `sources` (every record and merged row behind the card) and
    `group_size` on each group's card, `grouped_into` on the others.
    merged: live deal id -> source entries of rows merged into it.
    Returns counts."""
    candidates = [d for d in deals if d.get("deal_type") in GROUPED_TYPES]
    parent = {d["id"]: d["id"] for d in candidates}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # Bucket by state, buyer and CCN so this isn't all-pairs over ~950 rows
    buckets = {}
    for d in candidates:
        keys = {f"ccn:{c}" for c in d.get("ccns") or []}
        if d.get("buyer"):
            keys.add("buyer:" + name_key(d["buyer"]))
        if d.get("source_type") in ("rss", "edgar", "con"):
            keys |= {f"state:{st}" for st in d.get("states") or []}
        if d.get("source_type") in ("rss", "edgar") and d.get("seller"):
            keys.add("seller:" + (brand(d["seller"]) or name_key(d["seller"])))
        if d.get("acquisition_date") and d.get("facility_count") and d.get("source_type") in ("rss", "edgar"):
            keys.add(f"close:{sorted(d.get('states') or [])}:{d['acquisition_date']}:{d['facility_count']}")
        for k in keys:
            buckets.setdefault(k, []).append(d)
    reasons = {}
    for rows in buckets.values():
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                why = same_deal(a, b)
                if why and find(a["id"]) != find(b["id"]):
                    parent[find(a["id"])] = find(b["id"])
                    reasons[(a["id"], b["id"])] = why

    groups = {}
    for d in candidates:
        groups.setdefault(find(d["id"]), []).append(d)
    by_id = {d["id"]: d for d in deals}
    n_groups = 0
    for members in groups.values():
        # a state's after-the-fact record doesn't lead a CMS record of the same sale
        lead = max(members, key=lambda d: (STATUS_RANK.get(d.get("status"), 0), not after_closing(d),
                                           _completeness_score(d),
                                           d.get("created_at") or ""))
        # a one-home card doesn't lead a group of several homes when a card
        # about all of them is there (Advinia's 3 RI homes: the story leads)
        wider = [d for d in members if (d.get("facility_count") or 0) >= 2]
        if len(members) > 1 and (lead.get("facility_count") or 1) == 1 and wider:
            lead = max(wider, key=lambda d: (d.get("facility_count"), STATUS_RANK.get(d.get("status"), 0)))
        sources = [_source_entry(d) for d in sorted(members, key=lambda d: _source_entry(d)["date"] or "")]
        for d in members:
            sources += [{**m, "merged": True} for m in merged.get(d["id"], [])]
        lead["sources"] = sources
        lead["group_size"] = len(members)
        if len(members) > 1:
            n_groups += 1
            # Facilities from every record, for bed counts and history
            lead["group_ccns"] = sorted({c for d in members for c in d.get("ccns") or []})
            for d in members:
                if d is not lead:
                    d["grouped_into"] = lead["id"]
    n_groups += _group_ucc(deals, merged)
    _link_related(deals, find)
    for d in deals:
        if "sources" not in d:
            d["sources"] = [_source_entry(d)] + [{**m, "merged": True} for m in merged.get(d["id"], [])]
            d["group_size"] = 1
    return {"groups": n_groups, "grouped_records": sum(1 for d in deals if d.get("grouped_into")),
            "pairs": [(by_id[a].get("headline"), by_id[b].get("headline"), why) for (a, b), why in reasons.items()]}
