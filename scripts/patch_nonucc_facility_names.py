"""
scripts/patch_nonucc_facility_names.py

Patches missing facility_names on non-UCC deals that have article text in the DB
but returned empty facility_names from the original Claude extraction pass.

Uses a narrow targeted prompt — only asks for facility names, doesn't re-extract
everything. Runs async with a concurrency limit to avoid rate-limit errors.

Run:
  cd nursing-home-live-feed
  source .env && venv/bin/python3 scripts/patch_nonucc_facility_names.py

Flags:
  --dry-run      print what would change, no DB writes
  --concurrency  Claude API concurrency limit (default 5)
  --limit N      stop after N deals (for testing)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

import anthropic
import psycopg2
import psycopg2.extras

sys.path.insert(0, ".")
from config import get_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROMPT = """From the article below, extract the specific nursing home / senior care facility names mentioned.

Return a JSON array of facility name strings. Return [] if no specific facility names are mentioned.
Return ONLY the JSON array, no markdown.

Article:
{text}"""


def _load_deals(conn, limit: int | None) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT d.id, d.acquiring_entity, d.operator_names, d.states,
                   LEFT(a.raw_text, 6000) AS text_snippet
            FROM deals d
            JOIN articles a ON a.id = d.article_id
            WHERE (d.extraction_model != 'ucc_filing' OR d.extraction_model IS NULL)
              AND (d.facility_names IS NULL OR array_length(d.facility_names, 1) IS NULL)
              AND a.raw_text IS NOT NULL
              AND LENGTH(a.raw_text) > 200
            ORDER BY d.created_at
            """ + (f"LIMIT {int(limit)}" if limit else "")
        )
        return [dict(r) for r in cur.fetchall()]


async def _extract_facility_names(
    deal: dict,
    client: anthropic.AsyncAnthropic,
    sem: asyncio.Semaphore,
) -> tuple[dict, list[str]]:
    """Call Claude to extract facility names from article text."""
    async with sem:
        try:
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{
                    "role": "user",
                    "content": PROMPT.format(text=deal["text_snippet"])
                }]
            )
            raw = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            names = json.loads(raw)
            if not isinstance(names, list):
                names = []
            names = [n.strip() for n in names if isinstance(n, str) and n.strip()]
            return deal, names
        except Exception as e:
            logger.warning(f"Extraction failed for deal {deal['id']}: {e}")
            return deal, []


async def _run_async(deals: list[dict], concurrency: int, dry_run: bool, conn):
    config = get_config()
    async_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
    sem = asyncio.Semaphore(concurrency)

    logger.info(f"Running async extraction on {len(deals)} deals (concurrency={concurrency})")
    results = await asyncio.gather(
        *[_extract_facility_names(d, async_client, sem) for d in deals]
    )

    updated = 0
    empty = 0
    for deal, names in results:
        if names:
            entity = deal.get("acquiring_entity") or (deal.get("operator_names") or ["?"])[0]
            logger.info(f"  [{','.join(deal.get('states') or [])}] {entity[:40]:<40} → {names}")
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE deals SET facility_names = %s WHERE id = %s",
                        (names, deal["id"])
                    )
            updated += 1
        else:
            empty += 1

    if not dry_run:
        conn.commit()

    return updated, empty


def run(dry_run: bool, concurrency: int, limit: int | None):
    config = get_config()
    conn = psycopg2.connect(config.database_url)
    psycopg2.extras.register_uuid()

    try:
        deals = _load_deals(conn, limit)
        logger.info(f"Found {len(deals)} non-UCC deals with empty facility_names + article text")

        if not deals:
            logger.info("Nothing to patch.")
            return

        updated, empty = asyncio.run(_run_async(deals, concurrency, dry_run, conn))

        print(f"\n=== Patch summary ===")
        print(f"  Updated (found names):     {updated}")
        print(f"  No names found in article: {empty}")
        if dry_run:
            print("  [DRY RUN — no changes written]")

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="Process at most N deals")
    args = parser.parse_args()
    run(dry_run=args.dry_run, concurrency=args.concurrency, limit=args.limit)
