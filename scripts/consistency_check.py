"""
scripts/consistency_check.py

Check every record in a deals.json export against itself and CMS's list of
homes: the mistakes the 2026-10-21 audit found by hand, looked for on the
whole site at once. predeploy_check.py runs it, and stops a deploy when a
new or changed record has one of them.

    venv/bin/python3 scripts/consistency_check.py /tmp/deals.json

A record looked at and found right goes in consistency_exceptions.json with
the reason, so it isn't flagged again.
"""
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.cms_confirm import facility_names_agree
from pipeline.ucc_category import norm

EXCEPTIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "consistency_exceptions.json")


def load_exceptions() -> dict:
    with open(EXCEPTIONS_PATH) as f:
        return json.load(f)


def _linked_by_name(d, f) -> bool:
    """The record names the home it's linked to or its town, or one side of
    the sale is the home's owner in CMS's records (Eduro, owner of Cooney)."""
    names = (d.get("facility_names") or []) + [d.get("seller_entity") or ""]
    if any(facility_names_agree(n, f["name"]) for n in names if n):
        return True
    parties = {norm(d.get(k) or "") for k in ("seller_entity", "acquiring_entity")} - {""}
    if parties & {norm(o[0]) for o in f.get("owners") or []}:
        return True
    text = " ".join([d.get("summary") or "", d.get("source_title") or ""] + (d.get("facility_names") or [])).upper()
    return bool(f.get("city")) and re.search(rf"\b{re.escape(f['city'].upper())}\b", text) is not None


def issues(export: dict) -> list[tuple[str, str, str]]:
    """(check, deal id, detail) for every record that disagrees with itself
    or with CMS."""
    homes = export["facilities"]
    out = []
    latest = date.today().replace(year=date.today().year + 3).isoformat()
    by_home = defaultdict(list)
    for d in export["deals"]:
        if d.get("grouped_into"):
            continue
        src, ccns = d["source_type"], d.get("ccns") or []
        states = set(d.get("states") or [])
        for c in ccns:
            f = homes.get(c)
            if not f:
                continue  # a home that has left CMS's list
            # A UCC filing can be filed in the owner's state, not the home's
            if src != "ucc" and states and f["state"] not in states:
                out.append(("home_in_other_state", d["id"], f"{c} {f['name']} is in {f['state']}, record says {sorted(states)}"))
            # News and notices only link homes they name (CHOW and CMS
            # records come with the CCN; UCC links have their own rules)
            if (src in ("rss", "edgar", "gmail", "con") and d.get("extraction_model") != "cms_owner_change"
                    and d.get("match_type") not in ("chow_ccn_direct", "cms_ownership_direct")
                    and not _linked_by_name(d, f)):
                out.append(("home_not_named", d["id"], f"{c} {f['name']}, {f.get('city')}"))
        ad = d.get("acquisition_date") or ""
        if ad and not ("1990" <= ad[:10] <= latest):
            out.append(("date_out_of_range", d["id"], ad))
        if src != "ucc" and d.get("facility_count") and len(ccns) > d["facility_count"]:
            out.append(("more_homes_than_count", d["id"], f"{len(ccns)} homes linked, count {d['facility_count']}"))
        v = float(d["deal_value_m"]) if d.get("deal_value_m") not in (None, "") else None
        if v is not None and not (0 < v < 25000):
            out.append(("price_out_of_range", d["id"], f"${v}M"))
        b, s = norm(d.get("acquiring_entity") or ""), norm(d.get("seller_entity") or "")
        if b and b == s:
            out.append(("buyer_is_seller", d["id"], d["acquiring_entity"]))
        if d.get("deal_type") == "ownership_change" and ad and d.get("group_id") is None:
            for c in ccns:
                by_home[c].append(d)
    # Two sales of one home within four months, from the same kind of
    # source (a notice followed by its CHOW is the notice being confirmed)
    for c, ds in by_home.items():
        ds.sort(key=lambda d: d["acquisition_date"])
        for a, b in zip(ds, ds[1:]):
            gap = (date.fromisoformat(b["acquisition_date"][:10]) - date.fromisoformat(a["acquisition_date"][:10])).days
            if gap <= 120 and a["source_type"] == b["source_type"]:
                out.append(("two_sales_of_one_home", a["id"], f"{c}: also {b['id'][:8]}, {gap} days apart"))
    return out


def unexcused(found, exceptions: dict):
    """Issues not on a record already looked at: exceptions map a deal id
    (or its first 8 characters) to {"checks": [...], "why": "..."}."""
    return [(k, i, x) for k, i, x in found
            if not any(i.startswith(p) and k in v["checks"] for p, v in exceptions.items())]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    found = unexcused(issues(json.load(open(sys.argv[1]))), load_exceptions())
    for k, i, x in sorted(found):
        print(f"{k:22} {i[:8]}  {x}")
    print(f"\n{len(found)} issues")
