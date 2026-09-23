"""
scripts/backfill_deal_amounts.py

Re-extracts news articles whose deals have no amount at all (neither
deal_value_m nor financing_amount_m) and fills the missing amounts in.

Why (2026-09-23): article text used to be stored at a 10,000-char cap and
extracted at an 8,000-char cap, and trafilatura had been failing to import
(missing lxml_html_clean), so every article went through the BS4 fallback --
nav junk included. Long dealbook roundups lost their later deals, and
amounts in them were never seen.

Only fills NULL amounts on existing deals, and only when exactly one
re-extracted deal matches (same states, plus same facility count or a
shared entity/facility name). Never creates deals: re-extracted deals that
match nothing are written to --unmatched-report for review instead, since
those are candidates the old truncation dropped.

Usage (repo root):
    venv/bin/python3 scripts/backfill_deal_amounts.py --dry-run
    venv/bin/python3 scripts/backfill_deal_amounts.py --unmatched-report /tmp/unmatched.csv
"""
import argparse
import csv
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
import psycopg2.extras
from config import get_config
from scraper.rss import fetch_article_text
from pipeline.extractor import extract_deals


def _names(deal: dict) -> set[str]:
    vals = [deal.get("acquiring_entity"), deal.get("seller_entity"), deal.get("lender"),
            *(deal.get("facility_names") or []), *(deal.get("operator_names") or [])]
    return {v.strip().lower() for v in vals if v}


def _matches(existing: dict, extracted: dict) -> bool:
    if set(existing.get("states") or []) != set(extracted.get("states") or []):
        return False
    same_count = (existing.get("facility_count") is not None
                  and existing.get("facility_count") == extracted.get("facility_count"))
    return same_count or bool(_names(existing) & _names(extracted))


def run(dry_run: bool, unmatched_report: str | None):
    conn = psycopg2.connect(get_config().database_url)
    psycopg2.extras.register_uuid()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT a.id AS article_id, a.url, a.published_at, length(a.raw_text) AS old_len
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE s.source_type = 'rss'
              AND EXISTS (SELECT 1 FROM deals d WHERE d.article_id = a.id AND d.stage <> 'dismissed'
                          AND d.deal_value_m IS NULL AND d.financing_amount_m IS NULL)
            ORDER BY a.published_at DESC NULLS LAST
        """)
        articles = cur.fetchall()
    print(f"{len(articles)} articles to re-extract")

    filled, ambiguous, unmatched_rows, fetch_failed = 0, 0, [], 0
    for art in articles:
        text = fetch_article_text(art["url"])
        if not text:
            fetch_failed += 1
            print(f"  fetch failed: {art['url']}")
            continue
        extracted = extract_deals(text, art["url"], art["published_at"])

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, acquiring_entity, seller_entity, lender, facility_names, operator_names,
                       states, facility_count, deal_value_m, financing_amount_m
                FROM deals WHERE article_id = %s AND stage <> 'dismissed'
            """, (art["article_id"],))
            existing = cur.fetchall()

        used = set()
        for deal in existing:
            if deal["deal_value_m"] is not None or deal["financing_amount_m"] is not None:
                continue
            cands = [i for i, x in enumerate(extracted) if _matches(deal, x)]
            if len(cands) != 1:
                ambiguous += len(cands) > 1
                continue
            x = extracted[cands[0]]
            used.add(cands[0])
            value, financing = x.get("deal_value_m"), x.get("financing_amount_m")
            if value is None and financing is None:
                continue
            print(f"  fill {deal['id']} {deal['states']} n={deal['facility_count']}: "
                  f"value={value} financing={financing}")
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE deals SET deal_value_m = COALESCE(deal_value_m, %s),
                                         financing_amount_m = COALESCE(financing_amount_m, %s)
                        WHERE id = %s
                    """, (value, financing, deal["id"]))
            filled += 1

        # re-extracted deals matching no existing deal at all (not just the
        # amount-less ones) -- likely dropped by the old truncation
        for i, x in enumerate(extracted):
            if i not in used and not any(_matches(d, x) for d in existing):
                unmatched_rows.append([art["url"], x.get("acquiring_entity"), x.get("seller_entity"),
                                       ",".join(x.get("states") or []), x.get("facility_count"),
                                       x.get("deal_value_m"), x.get("financing_amount_m"),
                                       x.get("lender"), x.get("rationale")])

        if not dry_run:
            if len(text) > 0:
                with conn.cursor() as cur:
                    cur.execute("UPDATE articles SET raw_text = %s WHERE id = %s", (text, art["article_id"]))
            conn.commit()

    if unmatched_report:
        with open(unmatched_report, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["url", "acquirer", "seller", "states", "facility_count",
                        "deal_value_m", "financing_amount_m", "lender", "rationale"])
            w.writerows(unmatched_rows)

    conn.close()
    print(f"\nFilled {filled} deals | ambiguous {ambiguous} | fetch failed {fetch_failed} | "
          f"unmatched re-extracted deals {len(unmatched_rows)}" + (" [DRY RUN]" if dry_run else ""))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--unmatched-report", default=None)
    args = p.parse_args()
    run(args.dry_run, args.unmatched_report)
