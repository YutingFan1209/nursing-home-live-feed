"""
Build one labelling packet per sampled record: what the site shows, plus
the stored source text it was extracted from (article raw_text; UCC filing
the filing text for UCC records).

    venv/bin/python3 -m evals.packets evals/gold/sample.jsonl <out_dir>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

SHOWN = ["deal_type", "buyer", "seller", "facility_names", "ccns", "states", "facility_count",
         "acquisition_date", "deal_value_m", "financing_amount_m", "lender", "status", "facility_type",
         "headline", "summary", "source_url", "source_title", "published_at"]


def main() -> None:
    load_dotenv()
    sample, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    db = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = db.cursor()
    missing = 0
    for line in open(sample):
        r = json.loads(line)
        cur.execute("""select a.url, a.title, a.published_at, a.raw_text, d.extraction_model
                       from deals d join articles a on a.id = d.article_id where d.id = %s""", (r["id"],))
        row = cur.fetchone()
        if not row:
            missing += 1
        url, title, pub, text, model = row or (None, None, None, None, None)
        p = Path(out, f"{r['group']}_{r['id'][:8]}.txt")
        p.write_text(
            f"ID {r['id']}  group={r['group']}  model={model}\n"
            + "".join(f"SHOWN {k}: {r.get(k)}\n" for k in SHOWN)
            + f"\nSOURCE {url}\nTITLE {title}\nPUBLISHED {pub}\n\n{(text or '')[:12000]}\n")
    print(f"packets in {out}, {missing} without a source row")


if __name__ == "__main__":
    main()
