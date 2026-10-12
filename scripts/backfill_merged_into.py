"""
Set deals.merged_into for same-sale merges made before the column existed,
from the merge log data/same_sale_merges.jsonl (one line per merge, both
rows as they were). Idempotent.

    venv/bin/python3 scripts/backfill_merged_into.py
"""
import json
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
from config import get_config

LOG = "/Users/kitty/Projects/nursing-home-live-feed/data/same_sale_merges.jsonl"

if __name__ == "__main__":
    conn = psycopg2.connect(get_config().database_url)
    n = 0
    with conn, conn.cursor() as cur, open(LOG) as f:
        for line in f:
            m = json.loads(line)
            cur.execute("UPDATE deals SET merged_into = %s WHERE id = %s AND stage = 'dismissed' AND merged_into IS NULL",
                        (m["keep"]["id"], m["drop"]["id"]))
            n += cur.rowcount
    print(f"merged_into set on {n} deals")
