#!/usr/bin/env python3
"""
scripts/backfill_ny_xlsx.py

One-time backfill: runs UCC_Filings_Flagged_Analysis.xlsx through the same
process_article()/_process_ucc_filing() routing every other UCC source
uses, so NY data lands in `deals` the same way KY/NJ/ME data will —
confirmation vs. new-signal, RE/PE relevance check, dedup, all of it.

This is NOT added to discover_articles()'s regular loop — it's a one-time
historical import, not something that should re-run on every pipeline
execution. Run it once, then NY's live search adapter (if/when built,
following the same pattern as ucc/nj.py) handles ongoing updates.

Usage:
    python3 scripts/backfill_ny_xlsx.py /path/to/UCC_Filings_Flagged_Analysis.xlsx [--dry-run]

Run with --dry-run first to see the confirmation/new-signal/excluded
breakdown WITHOUT writing to the DB.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import argparse
import logging

import psycopg2

import main as pipeline
from ucc.ny import NewYorkUCCSource
from scraper.ucc import _filing_to_article

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run(xlsx_path: str, dry_run: bool):
    config = pipeline.get_config()
    filings = NewYorkUCCSource().bulk_ingest_from_xlsx(xlsx_path)
    logger.info(f"Loaded {len(filings)} unique NY UCC filings from {xlsx_path}")

    if dry_run:
        # Dry run still needs existing deals to check confirmation matches,
        # so it does connect read-only — just never writes.
        conn = psycopg2.connect(config.database_url)
        conn.set_session(readonly=True)
    else:
        conn = psycopg2.connect(config.database_url)

    from ucc.integrator import route_filing, RoutingDecision
    counts = {"confirmation": 0, "new_signal": 0, "excluded": 0}
    confirmed_examples, new_signal_examples = [], []

    try:
        existing_deals = pipeline._fetch_existing_deals_for_ucc_matching(conn)
        logger.info(f"Matching against {len(existing_deals)} existing deals")

        ucc_source_id = None
        if not dry_run:
            ucc_source_id = pipeline._ensure_source(
                type("S", (), {"name": "State UCC-1 Filings", "url": "ucc://multi-state", "source_type": "ucc"})(),
                conn,
            )

        for filing in filings:
            result = route_filing(filing, existing_deals)
            counts[result.decision.value] += 1

            if result.decision == RoutingDecision.CONFIRMATION and len(confirmed_examples) < 5:
                confirmed_examples.append((filing.debtor_name, filing.secured_party_name, result.matched_deal_id))
            if result.decision == RoutingDecision.NEW_SIGNAL and len(new_signal_examples) < 5:
                new_signal_examples.append((filing.debtor_name, filing.secured_party_name))

            if not dry_run:
                article = _filing_to_article(filing)
                article["source_id"] = ucc_source_id
                pipeline.process_article(article, conn)

        if not dry_run:
            conn.commit()

    finally:
        conn.close()

    logger.info("=" * 60)
    logger.info(f"{'DRY RUN — ' if dry_run else ''}Results:")
    logger.info(f"  Confirmations (existing deal updated): {counts['confirmation']}")
    logger.info(f"  New signals (new deal created):        {counts['new_signal']}")
    logger.info(f"  Excluded (not RE/PE relevant):         {counts['excluded']}")
    logger.info("=" * 60)
    if confirmed_examples:
        logger.info("Sample confirmations:")
        for debtor, lender, deal_id in confirmed_examples:
            logger.info(f"  {debtor} <- {lender}  (deal #{deal_id})")
    if new_signal_examples:
        logger.info("Sample new signals:")
        for debtor, lender in new_signal_examples:
            logger.info(f"  {debtor} <- {lender}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("xlsx_path")
    parser.add_argument("--dry-run", action="store_true", help="Show routing breakdown without writing to the DB")
    args = parser.parse_args()
    run(args.xlsx_path, args.dry_run)
