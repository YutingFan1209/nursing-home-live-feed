"""
Link a record to a CMS home after a person has checked it.

Shows the record's own text next to the home's CMS name, legal name and
address, and asks before writing. The link is stored as manual_reviewed
with an annotation saying why, so the export keeps it and
consistency_check.py doesn't flag it again: no exception to add later, no
--force at deploy time. The Atrium of Wayneview notice was approved on
2026-10-28 from a name match alone and was the wrong home; its address,
printed here, would have shown it.

    venv/bin/python3 scripts/approve_link.py <deal id or prefix> <ccn> "why"
    venv/bin/python3 scripts/approve_link.py <deal> <ccn> "why" --replace   # drop the record's other home links
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2

from config import get_config


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("deal")
    p.add_argument("ccn")
    p.add_argument("why")
    p.add_argument("--replace", action="store_true", help="remove the record's other home links")
    args = p.parse_args()

    conn = psycopg2.connect(get_config().database_url)
    cur = conn.cursor()
    cur.execute("""SELECT d.id::text, a.title, a.raw_text FROM deals d JOIN articles a ON a.id = d.article_id
                   WHERE d.id::text LIKE %s""", (args.deal + "%",))
    rows = cur.fetchall()
    if len(rows) != 1:
        sys.exit(f"{len(rows)} records start with {args.deal}")
    deal_id, title, text = rows[0]
    cur.execute("""SELECT provider_name, legal_business_name, provider_address, provider_city, provider_state
                   FROM cms_facilities WHERE ccn = %s""", (args.ccn,))
    home = cur.fetchone()
    if not home:
        sys.exit(f"CCN {args.ccn} is not in cms_facilities")
    name, legal, address, city, state = home
    cur.execute("SELECT ccn, provider_name, match_method FROM cms_matches WHERE deal_id = %s", (deal_id,))
    current = cur.fetchall()

    print(f"Record   {deal_id[:8]}  {title}")
    print(f"  text   {re.sub(r'\s+', ' ', text or '')[:600]}")
    print(f"Home     {args.ccn}  {name}")
    print(f"  legal  {legal or '-'}")
    print(f"  at     {address}, {city}, {state}")
    print("Linked now: " + ("; ".join(f"{c} {n} ({m})" for c, n, m in current) or "nothing"))
    if args.replace:
        print("--replace: the links above will be removed")
    if input("Write this link? [y/N] ").strip().lower() != "y":
        sys.exit("Nothing written")

    if args.replace:
        cur.execute("DELETE FROM cms_matches WHERE deal_id = %s AND ccn <> %s", (deal_id, args.ccn))
    cur.execute("DELETE FROM cms_matches WHERE deal_id = %s AND ccn = %s", (deal_id, args.ccn))
    cur.execute("""INSERT INTO cms_matches (id, deal_id, ccn, provider_name, provider_state, match_score,
                                            match_method, matched_on_field, verified, created_at)
                   VALUES (gen_random_uuid(), %s, %s, %s, %s, 100, 'manual_reviewed', 'manual', true, NOW())""",
                (deal_id, args.ccn, name, state))
    cur.execute("INSERT INTO annotations (id, deal_id, note, tag, created_at) VALUES (gen_random_uuid(), %s, %s, 'match', NOW())",
                (deal_id, f"CMS home linked by hand {date.today()}: {args.ccn} {name}, {address}, {city}. {args.why}"))
    conn.commit()
    print("Written")


if __name__ == "__main__":
    main()
