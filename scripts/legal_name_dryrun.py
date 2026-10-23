"""
Dry run of the matcher's legal-name rule (matcher.ownership._legal_name_match)
on every live UCC deal. Read-only session: nothing is linked or written.

  PYTHONPATH=. venv/bin/python3 scripts/legal_name_dryrun.py [out.json]

Prints how many unmatched UCC deals the rule would newly link, and how it
compares with the CMS matches the matched deals already have. With out.json,
also writes the proposed links (debtor names: keep it out of the repo).
"""
import collections
import json
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

from config import get_config
from matcher.ownership import _legal_name_match

conn = psycopg2.connect(get_config().database_url)
conn.set_session(readonly=True, autocommit=True)
with conn.cursor(cursor_factory=RealDictCursor) as cur:
    cur.execute("""
        SELECT d.id::text, d.states, d.operator_names, d.acquiring_entity, d.extraction_model,
               EXISTS (SELECT 1 FROM cms_matches m WHERE m.deal_id = d.id) AS matched,
               (SELECT array_agg(DISTINCT m.ccn) FROM cms_matches m WHERE m.deal_id = d.id AND m.ccn IS NOT NULL) AS ccns
        FROM deals d WHERE d.extraction_model = 'ucc_filing' AND d.stage <> 'dismissed'""")
    deals = cur.fetchall()
un = [d for d in deals if not d["matched"]]
ma = [d for d in deals if d["matched"]]
new = [(d, m) for d in un if (m := _legal_name_match(d, conn))]
chg = [(d, m) for d in ma if (m := _legal_name_match(d, conn))]
print("ucc deals", len(deals), "unmatched", len(un), "matched", len(ma))
print("NEW links among unmatched:", len(new), "by state", dict(collections.Counter(d["states"][0] for d, _ in new)))
same = sum(1 for d, m in chg if (d["ccns"] or []) == [m["ccn"]])
narrows = sum(1 for d, m in chg if m["ccn"] in (d["ccns"] or []) and len(d["ccns"]) > 1)
differs = [(d, m) for d, m in chg if m["ccn"] not in (d["ccns"] or [])]
print("matched deals the rule also hits:", len(chg), "| same single CCN", same,
      "| narrows a multi-CCN match to one", narrows, "| points at a CCN not in current matches", len(differs))
if len(sys.argv) > 1:
    json.dump({"new": [{"deal": d["id"], "state": d["states"][0], "debtor": (d["operator_names"] or [""])[0],
                        "ccn": m["ccn"], "provider": m["provider_name"], "legal": m["owner_name"]} for d, m in new],
               "differs": [{"deal": d["id"], "debtor": (d["operator_names"] or [""])[0], "current": d["ccns"],
                            "legal_ccn": m["ccn"], "provider": m["provider_name"]} for d, m in differs]},
              open(sys.argv[1], "w"), indent=1)
