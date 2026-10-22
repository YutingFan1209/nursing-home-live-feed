"""
How many UCC-only deals does the existing pipeline leave unresolved?

  venv/bin/python3 -m agent.baseline

Per state: UCC-only deals in the feed, how many have a CMS match (the matcher
already resolved them), how many don't (what the agent is for), and how many
of those join back to a ucc_filings row (what `agent.run --sample` can draw
from). Read-only.
"""
import psycopg2
from psycopg2.extras import RealDictCursor

from config import get_config

SQL = """
SELECT d.states[1] AS state,
       count(*) AS ucc_only_deals,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM cms_matches m WHERE m.deal_id = d.id)) AS matched,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM cms_matches m WHERE m.deal_id = d.id)) AS unmatched,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM cms_matches m WHERE m.deal_id = d.id)
                          AND EXISTS (SELECT 1 FROM ucc_filings uf
                                      WHERE uf.state = d.states[1]
                                        AND uf.filing_date = d.acquisition_date::text
                                        AND upper(uf.debtor_name) = upper(d.operator_names[1]))) AS sample_eligible
FROM deals d
WHERE d.extraction_model = 'ucc_filing' AND d.stage <> 'dismissed'
GROUP BY d.states[1]
ORDER BY unmatched DESC
"""


def main() -> None:
    conn = psycopg2.connect(get_config().database_url)
    conn.set_session(readonly=True, autocommit=True)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL)
        rows = cur.fetchall()
    print(f"{'state':<6}{'ucc-only':>10}{'matched':>10}{'unmatched':>11}{'sample-eligible':>17}")
    tot = dict.fromkeys(("ucc_only_deals", "matched", "unmatched", "sample_eligible"), 0)
    for r in rows:
        print(f"{str(r['state']):<6}{r['ucc_only_deals']:>10}{r['matched']:>10}{r['unmatched']:>11}{r['sample_eligible']:>17}")
        for k in tot:
            tot[k] += r[k]
    print(f"{'ALL':<6}{tot['ucc_only_deals']:>10}{tot['matched']:>10}{tot['unmatched']:>11}{tot['sample_eligible']:>17}")
    if tot["unmatched"] and not tot["sample_eligible"]:
        print("\nWARNING: unmatched deals exist but none join back to ucc_filings, so --sample will find nothing.")


if __name__ == "__main__":
    main()
