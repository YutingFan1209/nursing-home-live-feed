"""
Which lenders sit behind the UCC-only deals the matcher couldn't resolve?

  venv/bin/python3 -m agent.unmatched_lenders

Read-only. Shows each lender's count and what ucc/lender_classifier.py says
about it today, so a lender that is classified as a nursing home lender but
mostly files against non-nursing-home debtors stands out.
"""
import psycopg2
from psycopg2.extras import RealDictCursor

from config import get_config
from ucc.lender_classifier import classify_secured_party, to_confidence_label

SQL = """
SELECT d.lender, count(*) AS n,
       count(*) FILTER (WHERE d.stage = 'confirmed') AS confirmed
FROM deals d
WHERE d.extraction_model = 'ucc_filing' AND d.stage <> 'dismissed'
  AND NOT EXISTS (SELECT 1 FROM cms_matches m WHERE m.deal_id = d.id)
GROUP BY d.lender ORDER BY n DESC LIMIT 25
"""


def main() -> None:
    conn = psycopg2.connect(get_config().database_url)
    conn.set_session(readonly=True, autocommit=True)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL)
        rows = cur.fetchall()
    print(f"{'unmatched':>9}  {'label':<7} {'category':<17} lender")
    for r in rows:
        c = classify_secured_party(r["lender"] or "")
        print(f"{r['n']:>9}  {to_confidence_label(c):<7} {c.category.value:<17} {r['lender']}")


if __name__ == "__main__":
    main()
