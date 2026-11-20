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
pairs, a tenth of UCC cards). Two UCC records are grouped when they cover
exactly the same CMS facilities, name the same lender (boilerplate such as
"as agent" ignored) and were filed within UCC_SAME_LOAN_DAYS of each other.
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
separately are two transactions.

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
UCC_SAME_LOAN_DAYS = 3
LICENSEE_NEWS_DAYS = 90
SAME_STORY_DAYS = 45


def lender_key(lender: str | None) -> str:
    """A UCC lender less filing boilerplate; '' when the lender is unknown."""
    s = (lender or "").upper()
    if not s or s.startswith("NOT AVAILABLE"):
        return ""
    s = s.split(";")[0]
    s = re.sub(r",?\s+(ITS SUCCESSORS\b.*|AS (ADMINISTRATIVE |COLLATERAL )?AGENT\b.*|F/?K/?A\b.*)$", "", s)
    return name_key(s)


def ucc_same_loan(a: dict, b: dict) -> bool:
    """Two UCC filings that are one loan (property + operating company)."""
    if a.get("source_type") != "ucc" or b.get("source_type") != "ucc":
        return False
    if a.get("deal_type") not in UCC_LOAN_TYPES or b.get("deal_type") not in UCC_LOAN_TYPES:
        return False
    ca, cb = sorted(a.get("ccns") or []), sorted(b.get("ccns") or [])
    if not ca or ca != cb:
        return False
    la = lender_key(a.get("lender"))
    if not la or la != lender_key(b.get("lender")):
        return False
    da, db = _day(a), _day(b)
    return bool(da and db and abs((da - db).days) <= UCC_SAME_LOAN_DAYS)


def _group_ucc(deals: list[dict], merged: dict) -> int:
    """Group same-loan UCC filings (see module doc). Returns groups made."""
    rows = [d for d in deals if d.get("source_type") == "ucc" and d.get("deal_type") in UCC_LOAN_TYPES
            and d.get("ccns") and not d.get("grouped_into")]
    buckets = {}
    for d in rows:
        buckets.setdefault((tuple(sorted(d["ccns"])), lender_key(d.get("lender"))), []).append(d)
    n = 0
    for (_, lk), members in buckets.items():
        if not lk or len(members) < 2:
            continue
        members.sort(key=lambda d: _day(d) or date.max)
        # chain filings that are each within the window of the previous one
        runs, cur = [], [members[0]]
        for d in members[1:]:
            if ucc_same_loan(cur[-1], d):
                cur.append(d)
            else:
                runs.append(cur)
                cur = [d]
        runs.append(cur)
        for run in runs:
            if len(run) < 2:
                continue
            lead = max(run, key=lambda d: (UCC_LOAN_TYPES[d["deal_type"]], _completeness_score(d),
                                           d.get("created_at") or ""))
            sources = [_source_entry(d) for d in run]
            for d in run:
                sources += [{**m, "merged": True} for m in merged.get(d["id"], [])]
                if d is not lead:
                    d["grouped_into"] = lead["id"]
            debtors = list(dict.fromkeys(d["ucc_debtor"] for d in [lead] + run if d.get("ucc_debtor")))
            lead.update({"sources": sources, "group_size": len(run), "group_ccns": sorted(lead["ccns"]),
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
    licensee_parent = bool(shared) and "con" in kinds and bool(kinds & {"rss", "edgar"}) and (
        abs((da - db).days) <= LICENSEE_NEWS_DAYS)
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


def _source_entry(d: dict) -> dict:
    return {"id": d["id"], "source_type": d.get("source_type"), "source_name": d.get("source_name"),
            "source_url": d.get("source_url"), "source_title": d.get("source_title"),
            "date": d.get("acquisition_date") or (d.get("created_at") or "")[:10],
            "found_date": (d.get("created_at") or "")[:10], "status": d.get("status"),
            "headline": "".join(t for t, _ in d.get("headline") or [])}


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
    for d in deals:
        if "sources" not in d:
            d["sources"] = [_source_entry(d)] + [{**m, "merged": True} for m in merged.get(d["id"], [])]
            d["group_size"] = 1
    return {"groups": n_groups, "grouped_records": sum(1 for d in deals if d.get("grouped_into")),
            "pairs": [(by_id[a].get("headline"), by_id[b].get("headline"), why) for (a, b), why in reasons.items()]}
