"""
Main pipeline orchestrator.
Runs the full discovery → extraction → matching → alert cycle.

Usage:
  python main.py                          # normal daily run
  python main.py --dry-run               # run everything, write nothing to DB
  python main.py --test-article URL      # run one article end-to-end, print results
  python main.py --max-articles 10       # cap articles processed this run
"""

import argparse
import json
import logging
import logging.config
import signal
import sys
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone, timedelta

from config import get_config
from scraper.sources import get_active_sources
from scraper.rss import fetch_feed, fetch_article_text
from scraper.edgar import fetch_edgar_filings, fetch_filing_text
from scraper.chow import fetch_chow_deals, get_chow_source_id
from scraper.gmail_alerts import fetch_alert_articles
from scraper.ucc import fetch_ucc_filings
from pipeline.extractor import extract_deals
from pipeline.dedup import deduplicate_batch, is_duplicate, make_dedup_hash
from matcher.ownership import match_deal, determine_stage
from matcher.carecompare import enrich_matches, flag_policy_risks
from alerts.digest import send_daily_digest
from pipeline.normalizer import normalize_deal
from ucc.integrator import route_filing, ExistingDeal, RoutingDecision
config = get_config()


# ── Structured logging ────────────────────────────────────────
# JSON format in production (AWS CloudWatch), human-readable locally

def setup_logging():
    is_aws = bool(
        __import__("os").environ.get("AWS_LAMBDA_FUNCTION_NAME")
        or __import__("os").environ.get("AWS_EXECUTION_ENV")
    )

    if is_aws:
        # JSON structured logs for CloudWatch
        logging.basicConfig(
            level=logging.INFO,
            format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
    else:
        # Human-readable for local dev
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )


setup_logging()
logger = logging.getLogger(__name__)


# ── Cost estimation ───────────────────────────────────────────
# Rough Claude Sonnet pricing as of 2026 — update if pricing changes
COST_PER_ARTICLE_USD = 0.02   # ~$0.02 per article (input + output tokens avg)


def estimate_cost(article_count: int) -> str:
    estimated = article_count * COST_PER_ARTICLE_USD
    return f"~${estimated:.2f}"


# ── Signal handling ───────────────────────────────────────────

def _handle_shutdown(signum, frame):
    logger.info(f"Received signal {signum} — shutting down gracefully")
    sys.exit(0)


# ── Argument parsing ──────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Nursing Home Acquisition Alert Pipeline"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full pipeline but write nothing to the database",
    )
    parser.add_argument(
        "--test-article",
        metavar="URL",
        help="Run a single article URL through extraction and matching, print results",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=config.max_articles_per_run,
        metavar="N",
        help=f"Max articles to process this run (default: {config.max_articles_per_run})",
    )
    parser.add_argument(
        "--no-alerts",
        action="store_true",
        help="Skip sending the email digest this run",
    )
    return parser.parse_args()


# ── Main run ──────────────────────────────────────────────────

def run(dry_run=False, max_articles=None, no_alerts=False):
    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info(f"=== Nursing Home Acquisition Pipeline Starting [{mode}] ===")

    conn = psycopg2.connect(config.database_url)
    psycopg2.extras.register_uuid()

    try:
        # Step 1 — Discover new articles
        articles = discover_articles(conn)
        total_found = len(articles)
        logger.info(f"Discovered {total_found} new articles")

        # Apply max articles cap
        cap = max_articles or config.max_articles_per_run
        if len(articles) > cap:
            logger.warning(
                f"Article count ({len(articles)}) exceeds cap ({cap}) — "
                f"processing first {cap} only. "
                f"Estimated Claude cost for full batch: {estimate_cost(total_found)}"
            )
            articles = articles[:cap]
        
        logger.info(
            f"Processing {len(articles)} articles — "
            f"estimated Claude cost: {estimate_cost(len(articles))}"
        )

        if dry_run:
            logger.info("[DRY RUN] Would process the following articles:")
            for a in articles:
                logger.info(f"  {a.get('title', a['url'])}")
            logger.info("[DRY RUN] No data written to database")
            return

        # Step 2 — Extract deals from each article
        new_deals = 0
        for i, article in enumerate(articles, 1):
            logger.info(f"Processing article {i}/{len(articles)}: {article.get('title', article['url'])[:80]}")
            new_deals += process_article(article, conn)
            conn.commit()

        logger.info(f"Extracted and stored {new_deals} new deals")

        # Step 3 — Re-check pending deals
        rechecked = recheck_pending(conn)
        conn.commit()
        logger.info(f"Re-checked {rechecked} pending deals")

        # Step 4 — Send digest
        if not no_alerts:
            send_daily_digest(conn)
            conn.commit()
        else:
            logger.info("Skipping email digest (--no-alerts)")

        logger.info("=== Pipeline complete ===")

    except Exception as e:
        conn.rollback()
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        raise
    finally:
        conn.close()


# ── Test article mode ─────────────────────────────────────────

def run_test_article(url: str):
    """
    Fetch one article, run extraction and CMS matching, print results.
    Nothing is written to the database.
    Great for validating the pipeline on any article in ~30 seconds.
    """
    logger.info(f"=== TEST ARTICLE MODE: {url} ===")

    # Step 1 — Fetch article text
    logger.info("Fetching article text...")
    raw_text = fetch_article_text(url)
    if not raw_text:
        logger.error("Could not fetch article text — check URL or paywall")
        sys.exit(1)

    word_count = len(raw_text.split())
    logger.info(f"Fetched {word_count} words")

    if word_count < 300:
        logger.warning(f"Article is short ({word_count} words) — may be paywalled")

    # Step 2 — Extract deals
    logger.info("Running Claude extraction...")
    deals = extract_deals(raw_text, url)

    if not deals:
        logger.warning("No deals extracted from this article")
        print("\n=== EXTRACTION RESULT ===")
        print("No deals found")
        return

    print(f"\n=== EXTRACTION RESULT: {len(deals)} deal(s) found ===")
    for i, deal in enumerate(deals, 1):
        print(f"\n--- Deal {i} ---")
        print(json.dumps(deal, indent=2, default=str))

    # Step 3 — CMS matching (needs DB)
    logger.info("Running CMS matching...")
    try:
        conn = psycopg2.connect(config.database_url)
        psycopg2.extras.register_uuid()

        print("\n=== CMS MATCHING RESULTS ===")
        for i, deal in enumerate(deals, 1):
            matches = match_deal(deal, conn)
            matches = enrich_matches(matches, deal.get("states") or [], conn)
            matches = flag_policy_risks(matches)
            stage, confidence = determine_stage(matches)

            print(f"\nDeal {i}: {deal.get('acquiring_entity') or 'Unknown acquirer'}")
            print(f"  Stage: {stage} | Confidence: {confidence}")
            print(f"  CMS matches: {len(matches)}")

            for m in matches[:3]:
                flags = m.get("policy_flags") or []
                flag_str = f" ⚠ {', '.join(flags)}" if flags else ""
                print(
                    f"  [{m['match_score']}%] {m['provider_name']} "
                    f"(CCN: {m['ccn']}) — {m['provider_state']}{flag_str}"
                )

        conn.close()

    except Exception as e:
        logger.warning(f"CMS matching skipped — DB not available: {e}")
        print("(CMS matching skipped — DB connection failed)")

    print("\n=== END TEST ===")
    print("Nothing was written to the database.")


# ── Discovery ─────────────────────────────────────────────────

def discover_articles(conn) -> list[dict]:
    new_articles = []

    # RSS sources
    for source in get_active_sources("rss"):
        source_id = _ensure_source(source, conn)
        for art in fetch_feed(source.url):
            if not _article_exists(art["url"], conn):
                art["source_id"] = source_id
                new_articles.append(art)

    # EDGAR — new full-text search approach
    source_edgar = next(
        (s for s in get_active_sources("edgar")), None
    )
    if source_edgar:
        # Use one shared source record for all EDGAR filings
        edgar_source_id = _ensure_source(
            type("S", (), {"name": "SEC EDGAR Full-Text Search",
                           "url": "https://efts.sec.gov/LATEST/search-index",
                           "source_type": "edgar"})(),
            conn
        )
        for filing in fetch_edgar_filings():
            if not _article_exists(filing["url"], conn):
                filing["source_id"] = edgar_source_id
                new_articles.append(filing)

    # CHOW — quarterly CMS ownership change feed
    chow_source_id = get_chow_source_id(conn)
    chow_deals = fetch_chow_deals()
    for deal in chow_deals:
        if not _article_exists(deal["url"], conn):
            deal["source_id"] = chow_source_id
            new_articles.append(deal)

    # Gmail alerts — Google Alert emails sent to dedicated inbox
    try:
        gmail_source_id = _ensure_source(
            type("S", (), {"name": "Google Alerts (Gmail)",
                           "url": "gmail://googlealerts-noreply@google.com",
                           "source_type": "rss"})(),
            conn
        )
        alert_articles = fetch_alert_articles(days_back=2)
        for art in alert_articles:
            if not _article_exists(art["url"], conn):
                art["source_id"] = gmail_source_id
                new_articles.append(art)
        logger.info(f"Gmail alerts: {len(alert_articles)} articles found")
    except Exception as e:
        logger.warning(f"Gmail alerts skipped: {e}")

    # UCC-1 financing statements — state-level early acquisition signal,
    # runs as both confirmation of existing deals and a new source (see
    # process_article's ucc_filing branch for the routing logic)
    try:
        ucc_source_id = _ensure_source(
            type("S", (), {"name": "State UCC-1 Filings",
                           "url": "ucc://multi-state",
                           "source_type": "ucc"})(),
            conn
        )
        ucc_articles = fetch_ucc_filings(
            known_operator_names=_get_known_operator_names(conn),
            ky_bulk_file_path=getattr(config, "ky_ucc_bulk_file_path", None),
        )
        for art in ucc_articles:
            if not _article_exists(art["url"], conn):
                art["source_id"] = ucc_source_id
                new_articles.append(art)
        logger.info(f"UCC filings: {len(ucc_articles)} articles found")
    except Exception as e:
        logger.warning(f"UCC filing fetch skipped: {e}")

    return new_articles


# ── Article processing ────────────────────────────────────────

def process_article(article: dict, conn) -> int:
    article_id = _store_article(article, conn)

    # UCC-1 filings need routing (confirmation vs. new signal) instead of
    # the generic pre_extracted path below — a UCC-1 doesn't state an
    # acquirer/seller the way CHOW does, so it has to be matched against
    # existing deals first.
    if article.get("ucc_filing"):
        return _process_ucc_filing(article, article_id, conn)

    # CHOW deals are pre-extracted — skip Claude entirely
    if article.get("pre_extracted"):
        deal = {k: article[k] for k in [
            "acquiring_entity", "seller_entity", "operator_names",
            "facility_names", "states", "facility_count", "deal_value_m",
            "acquisition_date", "financing_amount_m", "lender", "rationale",
        ] if k in article}
        deal["extraction_model"] = "chow_direct"
        deals = [deal]
        deals = deduplicate_batch(deals)
        stored = 0
        for d in deals:
            hash_val = d.get("dedup_hash") or make_dedup_hash(d)
            if is_duplicate(hash_val, conn):
                continue
            deal_id = _store_deal(d, article_id, conn)
            _run_cms_matching(d, deal_id, conn)
            stored += 1
        _mark_extraction_done(article_id, conn)
        return stored

    raw_text = article.get("raw_text")
    if not raw_text:
        raw_text = fetch_article_text(article["url"])
        if raw_text:
            _update_article_text(article_id, raw_text, conn)

    if not raw_text:
        _mark_extraction_error(article_id, "No article text available", conn)
        return 0

    try:
        deals = extract_deals(raw_text, article["url"])
    except Exception as e:
        _mark_extraction_error(article_id, str(e), conn)
        return 0

    if not deals:
        _mark_extraction_done(article_id, conn)
        return 0

    deals = deduplicate_batch(deals)

    stored = 0
    for deal in deals:
        deal = normalize_deal(deal)
        hash_val = deal.get("dedup_hash") or make_dedup_hash(deal)
        if is_duplicate(hash_val, conn):
            logger.debug(f"Skipping duplicate: {deal.get('acquiring_entity')}")
            continue
        try:
            with conn.cursor() as sp:
                sp.execute("SAVEPOINT before_deal")
            deal_id = _store_deal(deal, article_id, conn)
            _run_cms_matching(deal, deal_id, conn)
            stored += 1
        except Exception as e:
            if 'unique constraint' in str(e).lower() or 'uniqueviolation' in type(e).__name__:
                logger.debug(f"Semantic duplicate skipped: {deal.get('acquiring_entity')} {deal.get('states')}")
                with conn.cursor() as sp:
                    sp.execute("ROLLBACK TO SAVEPOINT before_deal")
            else:
                raise

    _mark_extraction_done(article_id, conn)
    return stored


def _run_cms_matching(deal: dict, deal_id, conn):
    matches = match_deal(deal, conn)
    matches = enrich_matches(matches, deal.get("states") or [], conn)
    matches = flag_policy_risks(matches)
    stage, confidence = determine_stage(matches)

    if matches:
        _store_cms_matches(deal_id, matches, conn)

    recheck_after = None
    if stage in ("detected", "pending_cms"):
        from datetime import date
        recheck_after = date.today() + timedelta(days=config.recheck_interval_days)

    _update_deal_stage(deal_id, stage, confidence, recheck_after, conn)


# ── UCC filing routing ────────────────────────────────────────
# Confirms an existing deal OR seeds a new one, per the RE/PE lender
# scoping + "both confirmation and new source" decision from the 6/17
# meeting. See ucc/integrator.py for the matching logic itself.

def _get_known_operator_names(conn) -> list[str]:
    """NJ's UCC search only works by debtor name, not secured party — this
    feeds it a seed list pulled from operators already in the DB, rather
    than guessing names to search for."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT unnest(operator_names) AS name FROM deals
            WHERE operator_names IS NOT NULL
        """)
        return [row[0] for row in cur.fetchall() if row[0]]


def _fetch_existing_deals_for_ucc_matching(conn) -> list[ExistingDeal]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, operator_names, facility_names, acquisition_date, lender
            FROM deals
        """)
        return [
            ExistingDeal(
                id=row[0], operator_names=row[1] or [], facility_names=row[2] or [],
                acquisition_date=row[3], lender=row[4],
            )
            for row in cur.fetchall()
        ]


def _process_ucc_filing(article: dict, article_id, conn) -> int:
    filing = article["_ucc_filing_obj"]
    classification = article["_ucc_classification"]

    if not classification.is_acquisition_relevant:
        logger.debug(
            f"UCC filing excluded (not RE/PE relevant): "
            f"{filing.secured_party_name} [{classification.category.value}]"
        )
        _mark_extraction_done(article_id, conn)
        return 0

    existing_deals = _fetch_existing_deals_for_ucc_matching(conn)
    result = route_filing(filing, existing_deals)

    if result.decision == RoutingDecision.CONFIRMATION:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE deals
                SET ucc_confirmed = true,
                    lender = COALESCE(lender, %s)
                WHERE id = %s
            """, (filing.secured_party_name, result.matched_deal_id))
        logger.info(
            f"UCC confirmation: filing {filing.filing_number} ({filing.state}) "
            f"-> deal {result.matched_deal_id} (match score {result.match_score})"
        )
        _mark_extraction_done(article_id, conn)
        return 0

    if result.decision == RoutingDecision.NEW_SIGNAL:
        deal = {
            "acquiring_entity": None,
            "seller_entity": None,
            "operator_names": [filing.debtor_name],
            "facility_names": (
                [f.strip() for f in filing.collateral_description.split("|") if f.strip()]
                if filing.collateral_description
                else [filing.debtor_name]
            ),
            "states": [filing.state],
            "facility_count": None,
            "deal_value_m": None,
            "acquisition_date": filing.filing_date.isoformat() if filing.filing_date else None,
            "financing_amount_m": None,  # UCC-1s don't reliably disclose amount
            "lender": filing.secured_party_name,
            "extraction_model": "ucc_filing",
        }
        # Same pattern as the CHOW pre_extracted path — deduplicate_batch
        # populates dedup_hash on the dict, _store_deal reads it from there
        deals = deduplicate_batch([deal])
        if not deals:
            _mark_extraction_done(article_id, conn)
            return 0
        deal = deals[0]

        hash_val = deal.get("dedup_hash") or make_dedup_hash(deal)
        if is_duplicate(hash_val, conn):
            logger.debug(f"UCC new-signal skipped as duplicate: {filing.debtor_name}")
            _mark_extraction_done(article_id, conn)
            return 0

        try:
            with conn.cursor() as sp:
                sp.execute("SAVEPOINT before_ucc_deal")
            deal_id = _store_deal(deal, article_id, conn)
            with conn.cursor() as cur:
                cur.execute("UPDATE deals SET ucc_confirmed = true WHERE id = %s", (deal_id,))
            _run_cms_matching(deal, deal_id, conn)
            _mark_extraction_done(article_id, conn)
            return 1
        except Exception as e:
            if 'unique constraint' in str(e).lower() or 'uniqueviolation' in type(e).__name__:
                logger.debug(f"UCC new-signal semantic duplicate skipped: {filing.debtor_name}")
                with conn.cursor() as sp:
                    sp.execute("ROLLBACK TO SAVEPOINT before_ucc_deal")
                _mark_extraction_done(article_id, conn)
                return 0
            raise

    # Shouldn't reach here (EXCLUDED already handled above), but fail safe
    _mark_extraction_done(article_id, conn)
    return 0


def recheck_pending(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, acquiring_entity, seller_entity, operator_names,
                   facility_names, states, facility_count, deal_value_m,
                   acquisition_date, recheck_count
            FROM deals
            WHERE stage IN ('detected', 'pending_cms')
              AND (recheck_after IS NULL OR recheck_after <= CURRENT_DATE)
              AND recheck_count < %s
        """, (config.recheck_max_attempts,))
        cols = [d[0] for d in cur.description]
        pending = [dict(zip(cols, row)) for row in cur.fetchall()]

    count = 0
    for deal in pending:
        deal_id = deal.pop("id")
        recheck_count = deal.pop("recheck_count")
        _run_cms_matching(deal, deal_id, conn)
        _increment_recheck_count(deal_id, recheck_count + 1, conn)
        count += 1

    return count


# ── Database helpers ──────────────────────────────────────────

def _ensure_source(source, conn) -> str:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sources (name, url, source_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (url) DO UPDATE SET last_fetched_at = NOW()
            RETURNING id
        """, (source.name, source.url, source.source_type))
        return cur.fetchone()[0]


def _article_exists(url: str, conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM articles WHERE url = %s", (url,))
        return cur.fetchone() is not None


def _store_article(article: dict, conn) -> str:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO articles (source_id, url, title, published_at, raw_text)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (url) DO NOTHING
            RETURNING id
        """, (
            article.get("source_id"),
            article["url"],
            article.get("title"),
            article.get("published_at"),
            article.get("raw_text"),
        ))
        row = cur.fetchone()
        return row[0] if row else _get_article_id(article["url"], conn)


def _get_article_id(url: str, conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM articles WHERE url = %s", (url,))
        return cur.fetchone()[0]


def _update_article_text(article_id, text: str, conn):
    with conn.cursor() as cur:
        cur.execute("UPDATE articles SET raw_text = %s WHERE id = %s", (text, article_id))


def _mark_extraction_done(article_id, conn):
    with conn.cursor() as cur:
        cur.execute("UPDATE articles SET extraction_done = TRUE WHERE id = %s", (article_id,))


def _mark_extraction_error(article_id, error: str, conn):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE articles SET extraction_done = TRUE, extraction_error = %s WHERE id = %s",
            (error, article_id)
        )


def _store_deal(deal: dict, article_id, conn) -> str:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO deals (
                article_id, acquiring_entity, seller_entity, operator_names,
                facility_names, states, facility_count, deal_value_m,
                acquisition_date, financing_amount_m, lender,
                dedup_hash, extraction_model
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            ) RETURNING id
        """, (
            article_id,
            deal.get("acquiring_entity"),
            deal.get("seller_entity"),
            deal.get("operator_names") or [],
            deal.get("facility_names") or [],
            deal.get("states") or [],
            deal.get("facility_count"),
            deal.get("deal_value_m"),
            deal.get("acquisition_date"),
            deal.get("financing_amount_m"),
            deal.get("lender"),
            deal.get("dedup_hash"),
            deal.get("extraction_model"),
        ))
        return cur.fetchone()[0]


def _store_cms_matches(deal_id, matches: list[dict], conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM cms_matches WHERE deal_id = %s", (deal_id,))
        psycopg2.extras.execute_values(cur, """
            INSERT INTO cms_matches
                (deal_id, ccn, provider_name, owner_name, owner_type,
                 provider_state, ownership_start_date, match_score,
                 match_method, matched_on_field)
            VALUES %s
        """, [
            (deal_id, m["ccn"], m["provider_name"], m["owner_name"],
             m["owner_type"], m["provider_state"], m.get("ownership_start_date"),
             m["match_score"], m["match_method"], m["matched_on_field"])
            for m in matches
        ])


def _update_deal_stage(deal_id, stage: str, confidence, recheck_after, conn):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE deals SET stage = %s, confidence = %s, recheck_after = %s
            WHERE id = %s
        """, (stage, confidence, recheck_after, deal_id))


def _increment_recheck_count(deal_id, new_count: int, conn):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE deals SET recheck_count = %s WHERE id = %s",
            (new_count, deal_id)
        )


# ── Entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    args = parse_args()

    if args.test_article:
        run_test_article(args.test_article)
    else:
        run(
            dry_run=args.dry_run,
            max_articles=args.max_articles,
            no_alerts=args.no_alerts,
        )
