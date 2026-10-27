"""
One card per transaction: group exported records that report the same deal.

pipeline/same_sale.py merges duplicates in the database, but only when two
rows share a facility; news rarely names one, so the same sale still showed
as several cards (Genesis from McKnight's plus the Maine and North Carolina
filings; PACS-Eduro from two stories). This groups them for display only,
when deals.json is written. Nothing is merged or hidden in the database, and
every record keeps its own permalink.

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
STATUS_RANK = {"confirmed": 4, "on_file": 4, "reported": 3, "expected": 2, "unconfirmed": 1}
GROUPED_TYPES = ("ownership_change", "lease", "control_change")


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
    if ba and bb and not _agree(ba, bb) and not ratifies:
        return None
    if shared:
        return "same CMS facility"
    if not (ba and bb):
        return None
    sa, sb = a.get("seller"), b.get("seller")
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


# Words of a licensee's name that aren't its home's name or a naming template
_PLAIN = {"HEALTH", "HEALTHCARE", "CARE", "CENTER", "CENTRE", "REHAB", "REHABILITATION", "NURSING", "AND",
          "OF", "AT", "LLC", "INC", "LP", "LTD", "CORP", "PLLC"}
BATCH_MIN = 3


def batch_key(d: dict) -> tuple | None:
    """Licence-list records whose new licensees share a naming template
    ("Rest Haven SNF Opco LLC", "Parkway SNF Opco LLC") on one date: one
    owner moving many homes at once (2026-10-27: 32 Missouri homes to
    "... SNF OPCO LLC" on 6/1/2026). The template is the licensee's name
    less its home's and the previous licensee's words."""
    if "#licensee-" not in (d.get("source_url") or "") or not d.get("buyer") or not d.get("acquisition_date"):
        return None
    place = set(name_key(" ".join((d.get("facility_names") or []) + [d.get("seller") or ""])).split())
    rest = [w for w in name_key(d["buyer"]).split() if w not in place and w not in _PLAIN]
    return (tuple(d.get("states") or []), d["acquisition_date"], " ".join(rest)) if rest else None


def _batch_card(lead: dict, members: list[dict], key: tuple) -> None:
    """One card for a licence batch: the homes, not one home's licensee."""
    states, when, template = key
    homes = sorted({f for d in members for f in d.get("facility_names") or []})
    where = f" in {', '.join(states)}" if states else ""
    lead.update({
        "facility_names": homes, "facility_count": len(homes), "buyer": None, "seller": None,
        "buyer_disclosure": None, "seller_disclosure": None,
        "headline": [[f"{len(homes)} homes{where}", ""], [" move to new licensees named “… ", "muted"],
                     [template, ""], ["”", "muted"]],
    })
    if lead.get("status") == "reported":
        lead["status_detail"] = (
            f"Reported: state licence list shows {len(homes)} homes licensed to new “… {template}” "
            f"companies from {when}. One owner restructuring, or a sale of the group to one buyer; "
            "CMS doesn't show a new owner yet.")


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
        for k in keys:
            buckets.setdefault(k, []).append(d)
    batches = {}
    for d in candidates:
        if (k := batch_key(d)):
            batches.setdefault(k, []).append(d)
    batches = {k: rows for k, rows in batches.items() if len(rows) >= BATCH_MIN}
    reasons = {}
    for rows in batches.values():
        for b in rows[1:]:
            if find(rows[0]["id"]) != find(b["id"]):
                parent[find(rows[0]["id"])] = find(b["id"])
                reasons[(rows[0]["id"], b["id"])] = "same licence batch"
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
            key = next((k for k, rows in batches.items() if any(r is lead for r in rows)), None)
            if key:
                _batch_card(lead, batches[key], key)
    for d in deals:
        if "sources" not in d:
            d["sources"] = [_source_entry(d)] + [{**m, "merged": True} for m in merged.get(d["id"], [])]
            d["group_size"] = 1
    return {"groups": n_groups, "grouped_records": sum(1 for d in deals if d.get("grouped_into")),
            "pairs": [(by_id[a].get("headline"), by_id[b].get("headline"), why) for (a, b), why in reasons.items()]}
