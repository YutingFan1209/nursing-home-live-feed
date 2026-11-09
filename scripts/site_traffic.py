"""
scripts/site_traffic.py

Weekly visit summary for the live feed from GoatCounter (hefti-feed): page
views, records and homes opened, downloads, filters used, outbound clicks,
referrers and countries. Needs GOATCOUNTER_API_KEY in .env (a read-only
token from GoatCounter's Settings -> API).

    venv/bin/python3 scripts/site_traffic.py              # last 7 days
    venv/bin/python3 scripts/site_traffic.py --days 30

Visitors with ad blockers aren't counted, so these are a lower bound.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from dotenv import dotenv_values

BASE = "https://hefti-feed.goatcounter.com/api/v0"
KEY = (dotenv_values(Path(__file__).resolve().parent.parent / ".env").get("GOATCOUNTER_API_KEY") or "").strip()
# Hits that aren't visitors: the 2026-11-02 pixel test
IGNORE = {"test/pixel-check"}


def get(path: str, **q) -> dict:
    req = urllib.request.Request(f"{BASE}{path}?{urllib.parse.urlencode(q)}",
                                 headers={"Authorization": f"Bearer {KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"GoatCounter {path}: HTTP {e.code} {e.read()[:200]!r}")


def main() -> None:
    if not KEY:
        sys.exit("GOATCOUNTER_API_KEY missing from .env")
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 7
    end = date.today() + timedelta(days=1)
    rng = {"start": (end - timedelta(days=days)).isoformat(), "end": end.isoformat()}

    hits = [h for h in get("/stats/hits", limit=200, **rng).get("hits", []) if h["path"] not in IGNORE]
    groups = defaultdict(list)
    for h in hits:
        p = h["path"]
        kind = ("Records opened" if p.startswith("/record/") else "Homes opened" if p.startswith("/home/")
                else "Pages" if not h["event"] else p.split("/")[0].replace("-", " ").capitalize())
        groups[kind].append((h["count"], p))

    print(f"Site traffic {rng['start']} to {date.today()} (GoatCounter; ad-blocked visitors not counted)\n")
    views = sum(c for k in ("Pages", "Records opened", "Homes opened") for c, _ in groups.get(k, []))
    print(f"Page views: {views}")
    for kind in ("Pages", "Records opened", "Homes opened", "Download", "Filter", "Source click"):
        rows = sorted(groups.pop(kind, []), reverse=True)
        if rows:
            print(f"\n{kind} ({sum(c for c, _ in rows)}):")
            for c, p in rows[:15]:
                print(f"  {c:4}  {p}")
    for kind, rows in groups.items():
        print(f"\n{kind}: " + ", ".join(f"{p} ({c})" for c, p in rows))
    for name, label in (("toprefs", "Came from"), ("locations", "Countries"), ("locations/US", "US states"),
                        ("browsers", "Browsers"), ("systems", "Systems")):
        rows = [(s.get("name") or "direct / unknown", s["count"]) for s in get(f"/stats/{name}", **rng).get("stats", [])]
        if rows:
            print(f"\n{label}: " + ", ".join(f"{n} ({c})" for n, c in rows[:8]))


if __name__ == "__main__":
    main()
