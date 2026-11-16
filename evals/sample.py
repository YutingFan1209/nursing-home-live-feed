"""
Draw the gold-set sample from a site export (deals.json).

Stratified by source, then spread across months by systematic sampling
(sort by month, every k-th record from a seeded random start), so each
month gets its share. Records whose buyer or seller is a private person
(individual_filing) are left out: the repo is public.

    venv/bin/python3 -m evals.sample <export.json> [--seed N] > evals/gold/sample.jsonl
"""
from __future__ import annotations

import argparse
import json
import random

QUOTA = {"news": 70, "ucc": 35, "con": 25, "chow": 20}
GROUP = {"rss": "news", "edgar": "news", "ucc": "ucc", "con": "con", "chow": "chow"}


def month(r: dict) -> str:
    return (r.get("acquisition_date") or r.get("published_at") or r.get("found_date") or "")[:7]


def draw(records: list[dict], seed: int) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for group, n in QUOTA.items():
        pool = [r for r in records if GROUP.get(r.get("source_type")) == group and not r.get("individual_filing")]
        pool.sort(key=lambda r: (month(r), r["id"]))
        k = len(pool) / n
        start = rng.random() * k
        out += [dict(group=group, **pool[int(start + i * k)]) for i in range(min(n, len(pool)))]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("export")
    ap.add_argument("--seed", type=int, default=20261116)
    a = ap.parse_args()
    data = json.load(open(a.export))
    for r in draw(data["deals"], a.seed):
        print(json.dumps(r, default=str))


if __name__ == "__main__":
    main()
