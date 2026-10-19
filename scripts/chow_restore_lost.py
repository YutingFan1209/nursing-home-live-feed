"""
Restore CHOW records that were stored as articles but lost their deal.

Until 2026-10-19 two things dropped CHOW facilities: same buyer + state +
date rows hit the semantic unique index (one deal kept, "1 facility"), and
fuzzy dedup merged different facilities of one chain into one deal
(ACCURA HEALTHCARE OF O'NEILL into ... OF NELIGH). Both are fixed in
main.py / pipeline/dedup.py; this replays the orphaned CHOW articles
through the same pre-extracted path, which now adds same-day facilities to
their deal and keeps different facilities apart.

    venv/bin/python3 scripts/chow_restore_lost.py           # dry run
    venv/bin/python3 scripts/chow_restore_lost.py --apply
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import psycopg2.extras

import main
from scraper.chow import get_chow_source_id

FIELDS = {
    "chow_type": r"Type: ([^\n]*)",
    "date": r"Effective Date: ([0-9/]+)",
    "buyer": r"Buyer \(New Owner\): ([^\n]*)",
    "seller": r"Seller \(Prior Owner\): ([^\n]*)",
    "ccn": r"CCN\): (\d+)",
    "state": r"State: ([A-Z]{2})",
}


def orphaned_chow_articles(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.url, a.title, a.published_at, a.raw_text FROM articles a
            WHERE a.raw_text LIKE 'CMS Skilled Nursing Facility Change of Ownership Record%%'
              AND NOT EXISTS (SELECT 1 FROM deals d WHERE d.article_id = a.id)
            ORDER BY a.created_at, a.url
        """)
        return cur.fetchall()


def to_article(url, title, published_at, raw_text, source_id) -> dict:
    f = {k: (m.group(1).strip() if (m := re.search(p, raw_text)) else "") for k, p in FIELDS.items()}
    eff = datetime.strptime(f["date"], "%m/%d/%Y").date()
    return {
        "url": url, "title": title, "published_at": published_at, "raw_text": raw_text,
        "source_id": source_id, "source_type": "chow", "pre_extracted": True,
        "acquiring_entity": f["buyer"], "seller_entity": f["seller"] or None,
        "operator_names": [f["buyer"]], "facility_names": [],
        "states": [f["state"]] if f["state"] else [], "facility_count": 1,
        "deal_value_m": None, "acquisition_date": eff.isoformat(),
        "financing_amount_m": None, "lender": None,
        "rationale": f"CMS-verified {f['chow_type'].lower()} effective {f['date']}. "
                     f"Buyer: {f['buyer']}. Seller: {f['seller']}. CCN: {f['ccn']}.",
        "ccn": f["ccn"],
    }


def main_():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default: dry run, rolled back)")
    args = ap.parse_args()

    conn = psycopg2.connect(main.config.database_url)
    psycopg2.extras.register_uuid()
    source_id = get_chow_source_id(conn)
    rows = orphaned_chow_articles(conn)
    print(f"{len(rows)} CHOW articles without a deal")
    for row in rows:
        art = to_article(*row, source_id)
        main.process_article(art, conn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FROM articles a
            WHERE a.raw_text LIKE 'CMS Skilled Nursing Facility Change of Ownership Record%%'
              AND NOT EXISTS (SELECT 1 FROM deals d WHERE d.article_id = a.id)
              AND NOT EXISTS (SELECT 1 FROM cms_matches m
                              WHERE m.ccn = substring(a.raw_text from 'CCN\\): (\\d+)'))
        """)
        print(f"after: {cur.fetchone()[0]} CHOW facilities still on no deal")
    if args.apply:
        conn.commit()
        print("committed")
    else:
        conn.rollback()
        print("dry run, rolled back (--apply to write)")


if __name__ == "__main__":
    main_()
