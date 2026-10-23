"""
Freeze a holdout sample for the accuracy estimate. Read-only.

  venv/bin/python3 -m agent.holdout --n 50 --seed 20261022

Writes to agent/traces/holdout/ (git-ignored: debtor names):
  pool.csv      every eligible filing at freeze time, with its stratum
  holdout.csv   the sample, in draw order, with blank label columns
  manifest.json seed, n, pool size and sha256 of both files, excluded filings

Pool: the unresolved UCC-only deals agent.run --sample draws from, minus every
filing any earlier run touched (agent/traces/**/sample_summary.csv), since those
were looked at while the tools were being fixed.

Stratified by state x lender group with proportional allocation: the pool is
sorted by stratum and a seeded random key, then every k-th filing is taken from
a seeded start (systematic sampling), so each stratum gets its share to within
one filing. Never tune on these filings.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from pathlib import Path

from agent.run import sample_filings
from agent.tools import ToolBox

OUT = Path("agent/traces/holdout")


def lender_group(tb: ToolBox, name: str, state: str) -> str:
    c = tb.call("classify_lender", {"name": name or "", "state": state})
    if c["nursing_home_bank"]:
        return "nursing_home_bank"
    if c["small_business_or_filing_agent"]:
        return "small_business"
    return {"real_estate": "real_estate", "private_equity": "real_estate",
            "bank_general": "bank", "equipment_vendor": "vendor"}.get(c["category"], "other")


def used_filings() -> set:
    used = set()
    for p in Path("agent/traces").rglob("*.csv"):
        if OUT in p.parents:
            continue
        with p.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("state") and r.get("filing_number"):
                    used.add((r["state"], r["filing_number"]))
    return used


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, required=True)
    args = ap.parse_args(argv)
    if (OUT / "manifest.json").exists():
        raise SystemExit(f"{OUT}/manifest.json exists: the holdout is frozen. Move it aside to redraw.")

    tb = ToolBox.from_config()
    used = used_filings()
    pool = [f for f in sample_filings(tb.conn, 100000, None) if (f["state"], f["filing_number"]) not in used]
    # one row per filing (a filing can join two deal rows)
    pool = list({(f["state"], f["filing_number"]): f for f in pool}.values())
    for f in pool:
        f["lender_group"] = lender_group(tb, f["secured_party"], f["state"])
        f["stratum"] = f"{f['state']}|{f['lender_group']}"

    rng = random.Random(args.seed)
    pool.sort(key=lambda f: (f["state"], f["filing_number"]))      # fixed order before the seeded key
    for f in pool:
        f["rand_key"] = rng.random()
    pool.sort(key=lambda f: (f["stratum"], f["rand_key"]))
    k = len(pool) / args.n
    start = rng.random() * k
    picks = [pool[int(start + i * k)] for i in range(args.n)]
    rng.shuffle(picks)                                             # labeling order carries no stratum

    OUT.mkdir(parents=True, exist_ok=True)
    cols = ["state", "filing_number", "debtor_name", "secured_party", "filing_date", "filing_type",
            "collateral_description", "status", "detail_url", "lender_group", "stratum"]
    with (OUT / "pool.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols + ["rand_key"], extrasaction="ignore")
        w.writeheader()
        w.writerows(pool)
    label_cols = ["true_ccn", "label_source_url", "label_source_kind", "label_note", "confirmed_by_human"]
    with (OUT / "holdout.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["draw"] + cols + label_cols, extrasaction="ignore")
        w.writeheader()
        for i, f in enumerate(picks, 1):
            w.writerow({"draw": i, **f})
    manifest = {
        "note": "Draw v0 (same seed) was discarded before any agent run or labeling: its lender groups "
                "used wrong category names, so nearly every filing fell in 'other'. Kept in discarded_v0/.",
        "seed": args.seed, "n": args.n, "pool_size": len(pool), "excluded_used": sorted(map(list, used)),
        "method": "systematic sample over pool sorted by (state|lender_group, seeded key)",
        "pool_sha256": sha(OUT / "pool.csv"), "holdout_sha256": sha(OUT / "holdout.csv"),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    by = {}
    for f in picks:
        by[f["stratum"]] = by.get(f["stratum"], 0) + 1
    print(f"pool {len(pool)} (excluded {len(used)} used), drew {len(picks)} with seed {args.seed}")
    for s, c in sorted(by.items()):
        print(f"  {s:<28}{c}")
    print(f"frozen: {OUT}/holdout.csv  sha256 {manifest['holdout_sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
