"""
A person approves every news and SEC card before it goes on the site.

The deploy (scripts/deploy.py -> evals/holds.py) holds new news cards as
"awaiting_approval" even when the checker passes them. This script moves
them through the Feed Audit Review page:

    # 1. after a deploy: write the held news cards as review-page documents
    venv/bin/python3 -m evals.approval seed <export deals.json> <out dir>
       -> <out dir>/<id>.json per card + writes.json (ArtifactData batch ops)

    # 2. after the person reviews: their decisions, saved from the page's
    #    "reviews" collection as <dir>/<id>.json
    venv/bin/python3 -m evals.approval apply <reviews dir> <export deals.json>          # dry run
    venv/bin/python3 -m evals.approval apply <reviews dir> <export deals.json> --yes    # write

What apply does with each decision (the page's value for a field is the
checker's answer with the person's corrections laid over it):
  right / minor   released as is, plus any corrections the person typed
  wrong           the corrected fields written to the record, then released
  duplicate       merged into the card the checker named (left held if none)
  noise           dismissed (not a deal)
  uncertain       stays held
Every write keeps a backup row (data/handfix_backup_approval.jsonl) and adds
a public correction note. Corrections to fields that aren't stored columns
(deal type, status, facility type, CCNs) are printed for a hand override
in pipeline/deal_labels.py; such a card stays held until that's done.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from evals import holds as H

ROOT = Path(__file__).resolve().parent.parent
BACKUP = ROOT / "data" / "handfix_backup_approval.jsonl"
SITE = "https://yutingfan1209.github.io/nursing-home-live-feed/?id="
SHOWN = ["deal_type", "buyer", "seller", "lender", "facility_count", "states", "acquisition_date",
         "deal_value_m", "financing_amount_m", "status", "facility_type", "ccns"]
# page field -> deals column
COLUMNS = {"buyer": "acquiring_entity", "seller": "seller_entity", "lender": "lender",
           "facility_count": "facility_count", "states": "states", "acquisition_date": "acquisition_date",
           "deal_value_m": "deal_value_m", "financing_amount_m": "financing_amount_m"}
HAND = {"deal_type", "status", "facility_type", "ccns"}
PAGE = {c: f for f, c in COLUMNS.items()}   # deals column -> page field


def _connect():
    import psycopg2
    import psycopg2.extras
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    return conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _evidence(ids: list[str]) -> dict[str, dict]:
    """Quotes, second-read disagreements and grounding removals, from the
    database (kept off the public export)."""
    if not ids:
        return {}
    conn, cur = _connect()
    cur.execute("SELECT id::text AS id, field_quotes, second_read, ungrounded FROM deals WHERE id::text = ANY(%s)", (ids,))
    out = {r["id"]: r for r in cur.fetchall()}
    conn.close()
    return out


def _norm(v):
    if isinstance(v, list):
        return sorted(str(x).upper() for x in v) or None
    if v in ("", None):
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return str(v).strip().lower()


def seed(export_path: str, out: str) -> None:
    deals = {d["id"]: d for d in json.loads(Path(export_path).read_text())["deals"]}
    judged = H._jsonl(H.JUDGED)
    held = {i: h for i, h in H.load().items() if h.get("source_type") in H.APPROVE_SOURCES}
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    batch = f"approve-{datetime.now():%Y-%m-%d}"
    evidence = _evidence(list(held))
    writes, missing = [], []
    for n, (cid, h) in enumerate(held.items()):
        card, j = deals.get(cid), judged.get(cid, {})
        if not card:
            # held cards are left out of the export: export_deals.py --include-held keeps them
            missing.append(cid)
            continue
        ev = evidence.get(cid, {})
        second = ev.get("second_read") or {}
        # fields the second read disagreed on are shown as unsure
        unsure = set(j.get("unsure_fields") or []) | {PAGE.get(f, f) for f in (second.get("disagree") or {})}
        notes = [j.get("note") or ""]
        if second.get("disagree"):
            notes.append("Second read disagrees: " + "; ".join(
                f"{PAGE.get(f, f)} = {json.dumps(v, default=str)}" for f, v in second["disagree"].items()))
        if second and not second.get("matched", True):
            notes.append("The second read didn't find this deal at all.")
        unquoted = [PAGE.get(f, f) for f in (ev.get("field_quotes") or {}).get("_unquoted") or []]
        if unquoted:
            unsure |= set(unquoted)
            notes.append("No quote given for: " + ", ".join(unquoted) + ".")
        if ev.get("ungrounded"):
            notes.append("Blanked, not supported by the article: " + "; ".join(ev["ungrounded"]))
        doc = {"order": 3000 + n, "batch": batch, "group": "news", "source_type": card.get("source_type"),
               "headline": card.get("headline"), "shown": {f: card.get(f) for f in SHOWN},
               "claude": {"verdict": j.get("verdict") or h.get("verdict"), "gold": j.get("gold") or {},
                          "wrong_fields": j.get("wrong_fields") or [], "unsure_fields": sorted(unsure),
                          "note": " ".join(x for x in notes if x), "quote": j.get("quote"),
                          "duplicate_of": j.get("duplicate_of")},
               "quotes": {PAGE.get(f, f): q for f, q in (ev.get("field_quotes") or {}).items() if not f.startswith("_")},
               "source_url": card.get("source_url"), "site_url": SITE + cid}
        p = out_dir / f"{cid}.json"
        p.write_text(json.dumps(doc, default=str))
        writes.append({"op": "set", "collection": "cards", "doc_id": cid, "file_path": str(p.resolve())})
    (out_dir / "writes.json").write_text(json.dumps(writes))
    print(f"{len(writes)} held news card(s) written for batch {batch}: {out_dir / 'writes.json'}")
    if missing:
        print(f"{len(missing)} not in this export (export with --include-held): {', '.join(m[:8] for m in missing)}")


def _final(card: dict, judge: dict, review: dict) -> dict:
    """The page's answer: the checker's fields with the person's corrections on top."""
    return {**(judge.get("gold") or {}), **(review.get("fixes") or {})}


def plan(reviews_dir: str, export_path: str) -> list[dict]:
    deals = {d["id"]: d for d in json.loads(Path(export_path).read_text())["deals"]}
    judged = H._jsonl(H.JUDGED)
    held = {i: h for i, h in H.load().items() if h.get("source_type") in H.APPROVE_SOURCES}
    steps = []
    for cid, h in held.items():
        p = Path(reviews_dir) / f"{cid}.json"
        if not p.exists():
            continue
        review = json.loads(p.read_text())
        review = review.get("data", review)
        card, j = deals.get(cid) or {}, judged.get(cid, {})
        verdict = review.get("verdict") or j.get("verdict")
        final = _final(card, j, review) if verdict not in ("right",) or review.get("fixes") else {}
        changes = {f: final[f] for f in final
                   if f in COLUMNS or f in HAND
                   if _norm(final[f]) != _norm(card.get(f))
                   and (f in (j.get("wrong_fields") or []) or f in (review.get("fixes") or {}))}
        step = {"id": cid, "verdict": verdict, "headline": h.get("headline"), "note": review.get("note") or "",
                "columns": {COLUMNS[f]: v for f, v in changes.items() if f in COLUMNS},
                "hand": {f: v for f, v in changes.items() if f in HAND}}
        if verdict in ("right", "minor", "wrong"):
            step["action"] = "hand-then-release" if step["hand"] else "release"
        elif verdict == "duplicate":
            step["action"] = "merge" if j.get("duplicate_of") else "hold"
            step["into"] = j.get("duplicate_of")
        elif verdict == "noise":
            step["action"] = "dismiss"
        else:
            step["action"] = "hold"
        steps.append(step)
    return steps


def apply(steps: list[dict], yes: bool) -> None:
    for s in steps:
        extra = f" {s['columns']}" if s["columns"] else ""
        extra += f" HAND: {s['hand']}" if s["hand"] else ""
        extra += f" into {s['into'][:8]}" if s.get("into") else ""
        print(f"{s['id'][:8]} {s['verdict']:9} {s['action']:17}{extra}")
    if not yes:
        print("\nDry run. Rerun with --yes to write.")
        return
    conn, cur = _connect()
    holds = H.load()
    released = []
    with open(BACKUP, "a") as bk:
        for s in steps:
            if s["action"] == "hold":
                continue
            cur.execute("SELECT * FROM deals WHERE id = %s", (s["id"],))
            row = cur.fetchone()
            if not row:
                print(f"{s['id'][:8]}: not in the database, skipped")
                continue
            bk.write(json.dumps(row, default=str) + "\n")
            why = f"(news approval {datetime.now():%Y-%m-%d}) " + (s["note"] or "Corrected on review.")
            if s["columns"]:
                cols = ", ".join(f"{c} = %s" for c in s["columns"])
                cur.execute(f"UPDATE deals SET {cols}, updated_at = now() WHERE id = %s", (*s["columns"].values(), s["id"]))
                cur.execute("INSERT INTO annotations (deal_id, note, tag) VALUES (%s, %s, 'correction')", (s["id"], why))
            if s["action"] == "merge":
                cur.execute("SELECT id FROM deals WHERE id::text LIKE %s AND stage <> 'dismissed'", (s["into"][:8] + "%",))
                target = cur.fetchall()
                if len(target) != 1:
                    print(f"{s['id'][:8]}: duplicate target {s['into'][:8]} not found once, left held")
                    continue
                cur.execute("UPDATE deals SET merged_into = %s, stage = 'dismissed', updated_at = now() WHERE id = %s",
                            (target[0]["id"], s["id"]))
                cur.execute("INSERT INTO annotations (deal_id, note, tag) VALUES (%s, %s, 'correction')",
                            (s["id"], why + f" Same deal as {s['into'][:8]}; merged."))
            elif s["action"] == "dismiss":
                cur.execute("UPDATE deals SET stage = 'dismissed', updated_at = now() WHERE id = %s", (s["id"],))
                cur.execute("INSERT INTO annotations (deal_id, note, tag) VALUES (%s, %s, 'correction')",
                            (s["id"], why + " Not a nursing home deal."))
            if s["action"] in ("release", "merge", "dismiss"):
                released.append(s["id"])
    conn.commit()
    with open(H.RELEASED, "a") as f:
        for cid in released:
            f.write(json.dumps({"id": cid, "at": f"{datetime.now():%Y-%m-%d %H:%M}", "note": "news approval"}) + "\n")
            holds.pop(cid, None)
    H.save(holds)
    left = [s for s in steps if s["action"] in ("hold", "hand-then-release")]
    print(f"\n{len(released)} released/merged/dismissed; {len(left)} still held"
          + (" (hand overrides needed, then `python -m evals.holds release <id>`)" if left else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("export")
    s.add_argument("out")
    a_ = sub.add_parser("apply")
    a_.add_argument("reviews")
    a_.add_argument("export")
    a_.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    if a.cmd == "seed":
        seed(a.export, a.out)
    else:
        apply(plan(a.reviews, a.export), a.yes)


if __name__ == "__main__":
    sys.exit(main())
