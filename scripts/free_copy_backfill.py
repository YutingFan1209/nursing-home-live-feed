"""Free copies for the paywalled news cards already on the site
(pipeline/free_copy.py does new articles during each run).

    venv/bin/python3 scripts/free_copy_backfill.py            # paid: search + re-read, show changes
    venv/bin/python3 scripts/free_copy_backfill.py --apply    # free: apply the saved results

The first form searches and re-reads every live preview-only card and writes
data/free_copy_backfill.json with the proposed changes; nothing in the
database changes. --apply then writes those changes (filling or correcting
fields, never blanking one), stores the preview + free copy as the article
text, and leaves a correction note on each changed card.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from pipeline import free_copy

OUT = Path(__file__).resolve().parents[1] / "data" / "free_copy_backfill.json"
FIELDS = ["acquiring_entity", "seller_entity", "facility_names", "states", "facility_count",
          "deal_value_m", "acquisition_date", "financing_amount_m", "lender"]


def _live_previews(cur):
    cur.execute("""
        SELECT a.id::text, a.url, a.title, a.published_at, a.raw_text,
               json_agg(json_build_object('id', d.id::text, 'acquiring_entity', d.acquiring_entity,
                   'seller_entity', d.seller_entity, 'facility_names', d.facility_names, 'states', d.states,
                   'facility_count', d.facility_count, 'deal_value_m', d.deal_value_m,
                   'acquisition_date', d.acquisition_date, 'financing_amount_m', d.financing_amount_m,
                   'lender', d.lender))
        FROM articles a JOIN deals d ON d.article_id = a.id
        WHERE d.stage <> 'dismissed' AND d.merged_into IS NULL AND a.free_copy_url IS NULL
        GROUP BY a.id""")
    return [r for r in cur.fetchall() if free_copy.is_preview(r[4], r[1])]


def _same(a, b):
    if isinstance(a, list) or isinstance(b, list):
        return sorted(map(str, a or [])) == sorted(map(str, b or []))
    try:
        return abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return str(a or "").strip().lower() == str(b or "").strip().lower()


def search():
    from pipeline.extractor import extract_deals, USAGE, PRICES
    from config import get_config
    config = get_config()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    rows = _live_previews(cur)
    print(f"{len(rows)} live preview-only article(s)")
    results = []
    for aid, url, title, pub, raw, cards in rows:
        copy = free_copy.find(url, title, raw, pub)
        entry = {"article_id": aid, "url": url, "title": title, "free_copy": None, "changes": []}
        if copy:
            text = free_copy.combine(raw, copy)
            entry["free_copy"] = {k: copy[k] for k in ("url", "title", "why")}
            entry["text"] = text
            new = extract_deals(text, url, pub, title=title)
            for card in cards:
                # the re-read deal for this card: the one sharing its buyer, else the only one
                pick = [n for n in new if card["acquiring_entity"] and n.get("acquiring_entity")
                        and free_copy.anchors(card["acquiring_entity"], "") & free_copy.anchors(n["acquiring_entity"], "")]
                pick = pick or (new if len(new) == 1 and len(cards) == 1 else [])
                if not pick:
                    entry["changes"].append({"id": card["id"], "note": "re-read found no matching deal; unchanged"})
                    continue
                n = pick[0]
                diff = {f: n.get(f) for f in FIELDS
                        if n.get(f) not in (None, [], "") and not _same(n.get(f), card.get(f))}
                entry["changes"].append({"id": card["id"], "before": {f: card.get(f) for f in diff}, "after": diff})
        results.append(entry)
        print(f"- {url}\n  free copy: {entry['free_copy'] and entry['free_copy']['url']}")
        for c in entry["changes"]:
            print(f"  {c['id'][:8]}: {c.get('note') or (c['after'] and {f: (c['before'][f], c['after'][f]) for f in c['after']}) or 'no field changes'}")
    p = PRICES[config.claude_model]
    cost = (USAGE["input"] * p["input"] + USAGE["cache_read"] * p["cache_read"] + USAGE["cache_write"] * p["cache_write"]
            + USAGE["output"] * p["output"]) / 1e6 + USAGE.get("web_searches", 0) * 0.01
    print(f"\nSpent about ${cost:.2f} ({USAGE.get('web_searches', 0)} searches, {USAGE['calls']} calls)")
    OUT.write_text(json.dumps(results, indent=1, default=str))
    print(f"Saved to {OUT}; nothing changed in the database. --apply writes it.")


def apply():
    results = json.loads(OUT.read_text())
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    with conn, conn.cursor() as cur:
        for e in results:
            if not e["free_copy"]:
                continue
            cur.execute("UPDATE articles SET raw_text = %s, free_copy_url = %s, free_copy_title = %s WHERE id = %s",
                        (e["text"], e["free_copy"]["url"], e["free_copy"]["title"], e["article_id"]))
            for c in e["changes"]:
                if not c.get("after"):
                    continue
                cols = ", ".join(f"{f} = %s" for f in c["after"])
                cur.execute(f"UPDATE deals SET {cols}, updated_at = now() WHERE id = %s",
                            list(c["after"].values()) + [c["id"]])
                said = "; ".join(f"{f.replace('_', ' ')}: {c['before'].get(f) or 'blank'} → {v}" for f, v in c["after"].items())
                cur.execute("INSERT INTO annotations (deal_id, note, tag) VALUES (%s, %s, 'correction')",
                            (c["id"], f"Read again with a free copy of the paywalled story ({e['free_copy']['url']}): {said}"))
                print(f"{c['id'][:8]}: {said}")
    print("Applied.")


if __name__ == "__main__":
    apply() if "--apply" in sys.argv else search()
