"""
scripts/predeploy_check.py

Compare a new deals.json export against what's live before deploying, and
stop when something looks off. Every bad batch caught on 2026-10-12 would
have shown here: 306 consumer loans from one Kentucky run, weak fuzzy
matches moving records to the wrong homes, status flips from a rule change.

    venv/bin/python3 scripts/predeploy_check.py /tmp/deals.json            # vs the live site
    venv/bin/python3 scripts/predeploy_check.py /tmp/deals.json --force    # report only

Exits 1 when a check fails (unless --force). Each limit is a judgment call:
raise it when a big change is expected and has been reviewed.
"""
import json
import re
import sys
import urllib.request
from collections import Counter

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
from pipeline.deal_labels import CONSUMER_LENDER_RE
from pipeline.ucc_category import is_person
from scripts.consistency_check import issues, load_exceptions, unexcused

LIVE_URL = "https://yutingfan1209.github.io/nursing-home-live-feed/deals.json"

LIMITS = {
    "new_records": 150,           # new records in one deploy
    "removed_records": 25,        # records gone from the site
    "new_person_filings": 40,     # new UCC filings against a person
    "new_consumer_loans": 5,      # new filings from a consumer lender, any debtor
    "status_changes": 40,         # confirmed / expected / reported / unconfirmed flips
    "type_changes": 40,           # deal_type changes on existing records
    "facility_changes": 60,       # CCN changes on existing records
    "acquired_headlines": 0,      # never say "acquired" (2026-10-12 rule)
    "consistency_issues": 0,      # scripts/consistency_check.py, whole site (2026-10-21)
}


def headline(d):
    return "".join(t for t, _ in d.get("headline") or [])


def load_live():
    with urllib.request.urlopen(f"{LIVE_URL}?t=check", timeout=60) as r:
        return json.load(r)


def check(new: dict, live: dict) -> tuple[dict, list[str]]:
    a = {d["id"]: d for d in new["deals"]}
    b = {d["id"]: d for d in live["deals"]}
    added = [a[i] for i in a.keys() - b.keys()]
    removed = [b[i] for i in b.keys() - a.keys()]
    common = a.keys() & b.keys()
    counts = {
        "new_records": len(added),
        "removed_records": len(removed),
        "new_person_filings": sum(1 for d in added if d.get("source_type") == "ucc" and is_person(d.get("ucc_debtor"))),
        "new_consumer_loans": sum(1 for d in added if CONSUMER_LENDER_RE.search(d.get("lender") or "")),
        "status_changes": sum(1 for i in common if a[i].get("status") != b[i].get("status")),
        "type_changes": sum(1 for i in common if a[i].get("deal_type") != b[i].get("deal_type")),
        "facility_changes": sum(1 for i in common if sorted(a[i].get("ccns") or []) != sorted(b[i].get("ccns") or [])),
        "acquired_headlines": sum(1 for d in a.values() if re.search(r"\bacquired\b", headline(d))),
    }
    found = unexcused(issues(new), load_exceptions())
    counts["consistency_issues"] = len(found)
    lines = [f"{'check':22} {'count':>6} {'limit':>6}"]
    for k, v in counts.items():
        flag = "  <-- over" if v > LIMITS[k] else ""
        lines.append(f"{k:22} {v:6} {LIMITS[k]:6}{flag}")
    lines.append("")
    lines.append(f"new records by source/state: {dict(Counter((d.get('source_type'), (d.get('states') or ['?'])[0]) for d in added).most_common(12))}")
    lines.append(f"removed records by source: {dict(Counter(d.get('source_type') for d in removed))}")
    if added:
        lines.append("new lenders (top): " + "; ".join(f"{l} ({n})" for l, n in
                     Counter(d.get("lender") for d in added if d.get("lender")).most_common(10)))
        lines.append("sample new headlines:")
        lines += [f"  - {headline(d)[:110]}" for d in added[:12]]
    for k, i, x in found:
        lines.append(f"  {k}: {i[:8]} {headline(a[i])[:60]} | {x}")
    for d in a.values():
        if re.search(r"\bacquired\b", headline(d)):
            lines.append(f"  'acquired' headline: {d['id'][:8]} {headline(d)[:100]}")
    return counts, lines


if __name__ == "__main__":
    path = next((x for x in sys.argv[1:] if not x.startswith("--")), None)
    if not path:
        sys.exit(__doc__)
    counts, lines = check(json.load(open(path)), load_live())
    print("\n".join(lines))
    over = [k for k, v in counts.items() if v > LIMITS[k]]
    if over:
        print(f"\nCHECK FAILED: {', '.join(over)}. Review the above; rerun with --force to deploy anyway.")
        sys.exit(0 if "--force" in sys.argv else 1)
    print("\nAll checks passed.")
