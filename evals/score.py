"""
Score the feed against the hand-labelled gold set (evals/gold/labels.jsonl).

Two modes:

  stored   Free. Scores what a site export shows now, field by field.
           venv/bin/python3 -m evals.score stored <deals.json>

  extract  Paid (Claude API). Re-runs extraction on each gold news/SEC
           record's stored article text with the current prompt + model,
           and scores the fresh output. Prints a cost estimate and stops
           unless --yes is given.
           venv/bin/python3 -m evals.score extract [--yes] [--limit N]
           Saves the raw extractions; `rescore <file>` re-scores them free.

Gold record = one card as labelled on 2026-11-16 against its source:
  verdict        right | wrong | minor | duplicate | noise | uncertain
  gold           the correct value of every scored field
  wrong_fields   fields the site had wrong at labelling time
  unsure_fields  fields the labeller couldn't settle; never scored

A duplicate or noise card is "fixed" once the export no longer shows it; a
duplicate also once it is grouped onto the other record's card (grouped_into).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

GOLD = Path(__file__).parent / "gold" / "labels.jsonl"

# Fields an extraction can produce (extract mode scores only these).
EXTRACTED = ["buyer", "seller", "facility_count", "states", "acquisition_date",
             "deal_value_m", "financing_amount_m", "lender", "deal_type"]
SCORED = EXTRACTED + ["status", "facility_type", "ccns"]

_SUFFIX = re.compile(r"\b(llc|l\.l\.c|inc|incorporated|corp|corporation|co|company|lp|l\.p|ltd|limited|the|group|reit)\b")


def norm_name(v) -> str | None:
    if not v:
        return None
    s = _SUFFIX.sub(" ", str(v).lower())
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split()) or None


def same(field: str, got, want) -> bool:
    """Field-aware equality between a shown/extracted value and the gold one."""
    if field == "lender":
        # NJ's portal never gives the lender; the site says so in words.
        got = None if str(got or "").startswith("Not available") else got
        want = None if str(want or "").startswith("Not available") else want
        # the site drops filing boilerplate from lender text (export_deals.tidy_lender)
        from scripts.export_deals import tidy_lender
        got, want = tidy_lender(got), tidy_lender(want)
    if field in ("buyer", "seller", "lender"):
        return norm_name(got) == norm_name(want)
    if field in ("deal_value_m", "financing_amount_m"):
        if got in (None, "") or want in (None, ""):
            return got in (None, "") and want in (None, "")
        return abs(float(got) - float(want)) <= 0.05
    if field in ("states", "ccns"):
        return set(got or []) == set(want or [])
    if field == "facility_count":
        return (int(got) if got not in (None, "") else None) == (int(want) if want not in (None, "") else None)
    if field == "acquisition_date":
        return (str(got)[:10] if got else None) == (str(want)[:10] if want else None)
    return (got or None) == (want or None)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load_gold(path: Path = GOLD) -> list[dict]:
    return [json.loads(l) for l in open(path) if l.strip()]


def score_records(gold: list[dict], shown: dict[str, dict], fields: list[str]) -> dict:
    """shown: gold id -> the record to score (missing = card no longer shown).

    Returns per-group record accuracy and per-field accuracy."""
    rec = defaultdict(lambda: [0, 0])            # group -> [right, total]
    fld = defaultdict(lambda: [0, 0])            # field -> [right, total]
    misses = []
    for g in gold:
        grp = g["group"]
        cur = shown.get(g["id"])
        if g["verdict"] in ("duplicate", "noise"):
            # a duplicate is fixed once it shows on the other record's card
            ok = cur is None or (g["verdict"] == "duplicate" and bool(cur.get("grouped_into")))
            rec[grp][0] += ok
            rec[grp][1] += 1
            if not ok:
                misses.append((g["id"][:8], grp, f"{g['verdict']} still shown", None, g["note"]))
            continue
        if cur is None:                          # a real deal that disappeared
            rec[grp][1] += 1
            misses.append((g["id"][:8], grp, "card missing", None, g["note"]))
            continue
        all_ok = True
        for f in fields:
            if f in g["unsure_fields"]:
                continue
            ok = same(f, cur.get(f), g["gold"].get(f))
            fld[f][0] += ok
            fld[f][1] += 1
            if not ok:
                all_ok = False
                misses.append((g["id"][:8], grp, f, cur.get(f), g["gold"].get(f)))
        rec[grp][0] += all_ok
        rec[grp][1] += 1
    return {"records": dict(rec), "fields": dict(fld), "misses": misses}


def report(res: dict, title: str) -> None:
    print(f"\n{title}\n")
    print("Records fully right (duplicate/noise cards count right once hidden):")
    tot = [0, 0]
    for grp, (k, n) in sorted(res["records"].items()):
        lo, hi = wilson(k, n)
        print(f"  {grp:6} {k:3}/{n:<3} {k / n:5.0%}   95% CI {lo:.0%}-{hi:.0%}")
        tot[0] += k
        tot[1] += n
    lo, hi = wilson(*tot)
    print(f"  {'all':6} {tot[0]:3}/{tot[1]:<3} {tot[0] / tot[1]:5.0%}   95% CI {lo:.0%}-{hi:.0%}"
          "   (unweighted: news is oversampled)")
    print("\nFields:")
    for f, (k, n) in sorted(res["fields"].items(), key=lambda x: x[1][0] / max(x[1][1], 1)):
        print(f"  {f:20} {k:3}/{n:<3} {k / n:5.0%}")
    if res["misses"]:
        print(f"\nMisses ({len(res['misses'])}):")
        for m in res["misses"]:
            if m[3] is None and m[2].endswith(("still shown", "missing")):
                print(f"  {m[0]} {m[1]:5} {m[2]:22} {m[4][:100]}")
            else:
                print(f"  {m[0]} {m[1]:5} {m[2]:22} shown={m[3]!r:.60} gold={m[4]!r:.80}")


def score_export(path) -> dict:
    """Score a site export (deals.json) against the gold set."""
    gold = load_gold()
    deals = json.load(open(path))["deals"]
    by_id = {d["id"]: d for d in deals}
    # A grouped multi-source card keeps one id; its members' ids live in d["sources"].
    for d in deals:
        for s in d.get("sources") or []:
            if isinstance(s, dict) and s.get("id"):
                by_id.setdefault(s["id"], d)
    shown = {g["id"]: by_id[g["id"]] for g in gold if g["id"] in by_id}
    return score_records(gold, shown, SCORED)


def summary(res: dict) -> dict:
    """{group: [right, total], ..., "all": [right, total]} for logging."""
    out = {g: list(v) for g, v in res["records"].items()}
    out["all"] = [sum(v[0] for v in res["records"].values()), sum(v[1] for v in res["records"].values())]
    return out


def cmd_stored(args) -> None:
    report(score_export(args.export), f"Stored-data score vs gold ({args.export})")


# ---- extract mode (paid) ---------------------------------------------------

# Sonnet-class pricing, $ per million tokens; only used for the estimate.
PRICE_IN, PRICE_OUT = 3.00, 15.00


def _best_match(extracted: list[dict], want: dict) -> dict | None:
    """Pick the extracted deal that is the gold card (articles hold several)."""
    def overlap(d):
        s = 0
        s += 3 * (norm_name(d.get("acquiring_entity")) == norm_name(want.get("buyer")) and bool(want.get("buyer")))
        s += 3 * (norm_name(d.get("seller_entity")) == norm_name(want.get("seller")) and bool(want.get("seller")))
        s += 2 * bool(set(d.get("states") or []) & set(want.get("states") or []))
        s += 2 * (d.get("facility_count") == want.get("facility_count") and want.get("facility_count") is not None)
        for f in ("deal_value_m", "financing_amount_m"):
            s += 2 * (want.get(f) is not None and same(f, d.get(f), want.get(f)))
        return s
    scored = sorted(extracted, key=overlap, reverse=True)
    return scored[0] if scored and overlap(scored[0]) > 0 else None


RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "evals"


def _score_raw(gold: list[dict], raw: dict[str, list[dict]]) -> dict:
    """Map each article's fresh extraction to what the site would show, and score it.
    raw: gold id -> the deals extract_deals returned for that article."""
    from pipeline.deal_labels import _looks_like_description, transaction_type
    from scripts.export_deals import _news_deal_type
    name = lambda v: None if _looks_like_description(v) else v   # the site shows these as descriptions
    shown = {}
    for g in gold:
        m = _best_match(raw[g["id"]], g["gold"])
        if not m:
            continue
        m = {**m, "source_type": "rss", "summary": m.get("rationale")}   # stored as the card summary
        m["deal_type"] = _news_deal_type(m)    # what export_deals does for a news card
        shown[g["id"]] = {
            "buyer": name(m.get("acquiring_entity")), "seller": name(m.get("seller_entity")),
            "facility_count": m.get("facility_count"), "states": m.get("states"),
            "acquisition_date": m.get("acquisition_date"), "deal_value_m": m.get("deal_value_m"),
            "financing_amount_m": m.get("financing_amount_m"), "lender": m.get("lender"),
            "deal_type": transaction_type(m),
        }
    return score_records(gold, shown, EXTRACTED)


def _news_gold(limit=None) -> list[dict]:
    gold = [g for g in load_gold() if g["group"] == "news" and g["verdict"] not in ("duplicate", "noise")]
    return gold[:limit] if limit else gold


def cmd_rescore(args) -> None:
    """Free: rescore a saved extract run (after a scorer or display fix)."""
    raw = {r["id"]: r["deals"] for r in map(json.loads, open(args.raw))}
    gold = [g for g in _news_gold() if g["id"] in raw]
    report(_score_raw(gold, raw), f"Fresh-extraction score vs gold, rescored from {args.raw}")


def cmd_extract(args) -> None:
    import os
    from datetime import date
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    gold = _news_gold(args.limit)
    db = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = db.cursor()
    texts = {}
    for g in gold:
        cur.execute("""select a.raw_text, a.url, a.published_at from deals d
                       join articles a on a.id = d.article_id where d.id = %s""", (g["id"],))
        row = cur.fetchone()
        if row and row[0]:
            texts[g["id"]] = row
    from config import get_config
    max_chars = get_config().article_max_chars
    in_tok = sum(min(len(t[0]), max_chars) for t in texts.values()) / 4 + 3000 * len(texts)
    est = in_tok / 1e6 * PRICE_IN + 800 * len(texts) / 1e6 * PRICE_OUT
    print(f"{len(texts)} articles, ~{in_tok / 1e3:.0f}k input tokens, estimated ${est:.2f}"
          f" (model {get_config().claude_model})")
    if not args.yes:
        print("Not run. Add --yes to spend it.")
        return

    import logging
    from pipeline.extractor import extract_deals

    class _Failures(logging.Handler):           # extract_deals logs API errors and returns []
        def __init__(self):
            super().__init__(logging.ERROR)
            self.msgs = []

        def emit(self, record):
            self.msgs.append(record.getMessage())

    fails = _Failures()
    logging.getLogger("pipeline.extractor").addHandler(fails)
    raw_path = RAW_DIR / f"extract_raw_{date.today()}.jsonl"   # gitignored: names people
    raw, failed = {}, 0
    with open(raw_path, "w") as f:
        for i, g in enumerate(gold, 1):
            if g["id"] not in texts:
                continue
            text, url, pub = texts[g["id"]]
            fails.msgs.clear()
            out = extract_deals(text, url, pub)
            if fails.msgs:
                failed += 1
                print(f"  [{i}/{len(gold)}] {g['id'][:8]} API error: {fails.msgs[0][-160:]}", file=sys.stderr)
                if "credit balance" in fails.msgs[0]:
                    print("Stopped: the API account is out of credit.", file=sys.stderr)
                    break
                continue
            raw[g["id"]] = out
            f.write(json.dumps({"id": g["id"], "deals": out}, default=str) + "\n")
            print(f"  [{i}/{len(gold)}] {g['id'][:8]} {len(out)} deal(s)", file=sys.stderr)
    if len(raw) < len(texts):
        print(f"\nScored {len(raw)} of {len(texts)} articles ({failed} API errors left out).")
    print(f"Raw extractions saved to {raw_path} (rescore free: python -m evals.score rescore <file>)")
    res = _score_raw([g for g in gold if g["id"] in raw], raw)
    report(res, "Fresh-extraction score vs gold (news/SEC)")
    if not args.limit and len(raw) == len(texts):        # a full run measures this prompt
        from evals import record_measurement
        ok = summary(res).get("news", [0, 0])
        record_measurement("extract", f"{ok[0]}/{ok[1]} news cards fully right", raw_path.name)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stored")
    s.add_argument("export")
    e = sub.add_parser("extract")
    e.add_argument("--yes", action="store_true", help="actually call the API")
    e.add_argument("--limit", type=int)
    r = sub.add_parser("rescore")
    r.add_argument("raw")
    a = ap.parse_args()
    {"stored": cmd_stored, "extract": cmd_extract, "rescore": cmd_rescore}[a.cmd](a)


if __name__ == "__main__":
    main()
