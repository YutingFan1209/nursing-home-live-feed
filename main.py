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
import asyncio
import json
import logging
import logging.config
import signal
import sys
import psycopg2
import psycopg2.errors
import psycopg2.extras
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from config import get_config
from scraper.sources import get_active_sources
from scraper.rss import fetch_feed, fetch_article_text
from scraper.edgar import fetch_edgar_filings, fetch_filing_text
from scraper.chow import fetch_chow_deals, get_chow_source_id, get_chow_operator_names
from scraper.cms_owner_changes import (
    fetch_cms_owner_change_deals, refresh_ownership_if_stale,
    ensure_source as ensure_cms_owner_change_source,
)
from scraper.gmail_alerts import fetch_alert_articles
from scraper.con_al import fetch_con_al_notices, CON_AL_SOURCE_NAME, CON_AL_INDEX_URL
from scraper.con_ok import fetch_con_ok_deals, CON_OK_SOURCE_NAME, CON_OK_INDEX_URL
from scraper.con_me import fetch_con_me_cases, CON_ME_SOURCE_NAME, CON_ME_INDEX_URL
from scraper.con_mi import fetch_con_mi_deals, CON_MI_SOURCE_NAME, CON_MI_INDEX_URL
from scraper.con_ms import fetch_con_ms_deals, CON_MS_SOURCE_NAME, CON_MS_INDEX_URL
from scraper.con_nc import fetch_con_nc_deals, CON_NC_SOURCE_NAME, CON_NC_INDEX_URL
from scraper.con_md import fetch_con_md_cases, CON_MD_SOURCE_NAME, CON_MD_INDEX_URL
from scraper.con_nj import fetch_con_nj, CON_NJ_SOURCE_NAME, CON_NJ_OPERATOR_URL
from pipeline.source_health import log_source_health
from scraper.ucc import fetch_ucc_filings
from pipeline.extractor import extract_deals
from pipeline.dedup import deduplicate_batch, is_duplicate, make_dedup_hash, find_and_resolve_fuzzy_duplicate
from pipeline.excluded_urls import EXCLUDED_URLS, EXCLUDED_DOMAINS, EXCLUDED_PATTERNS
from pipeline.al_mc_scope import is_out_of_scope
from matcher.ownership import match_deal, determine_stage
from matcher.carecompare import enrich_matches, flag_policy_risks
from alerts.digest import send_daily_digest
from pipeline.normalizer import normalize_deal
from ucc.integrator import route_filing, ExistingDeal, RoutingDecision
from ucc.audit_log import save_ucc_filings
from pipeline.run_health import health
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
    parser.add_argument(
        "--skip-ucc",
        action="store_true",
        help="Skip UCC-1 filing scraping (RSS/EDGAR/CHOW/Gmail alerts still run) — "
             "use when UCC has already run today and you just want news/alert ingestion",
    )
    parser.add_argument(
        "--gmail-only",
        action="store_true",
        help="Only check Gmail alerts — skips RSS/EDGAR/CHOW/UCC entirely. "
             "Fastest way to check for new deals since the last run.",
    )
    parser.add_argument(
        "--ucc-only",
        action="store_true",
        help="Only scrape UCC-1 filings (pair with --ucc-states) -- skips Gmail/RSS/"
             "EDGAR/CHOW/CON. Lets several single-state UCC runs go in parallel: a "
             "full run upserts every sources row and holds those row locks until its "
             "whole UCC scrape returns, so a second concurrent full run blocks behind it.",
    )
    parser.add_argument(
        "--gmail-days-back",
        type=int,
        default=None,
        metavar="N",
        help="Override the Gmail alert lookback window (days). Normally auto-scaled "
             "from sources.last_fetched_at; use this to manually backfill after a "
             "longer gap (e.g. the tracked timestamp was reset by an intermediate run).",
    )
    parser.add_argument(
        "--ucc-states",
        type=str,
        default=None,
        metavar="NY,KY,OH",
        help="Restrict UCC-1 scraping to these comma-separated states (case-insensitive) "
             "instead of every ENABLE_*_PLAYWRIGHT-flagged state. Only scopes the UCC step "
             "-- RSS/EDGAR/CHOW/Gmail are unaffected, and this has no effect if --skip-ucc "
             "or --gmail-only is set (both skip UCC entirely, before this is even checked). "
             "Run states separately (e.g. one cron/launchd job per state) rather than "
             "bundled: each state's automation has different failure modes and runtimes "
             "(KY's same-day rate limit, NY's multi-hour individual-name list, OH's 429s), "
             "and nothing commits to the DB until the whole UCC fetch call returns -- so one "
             "state hanging or getting blocked loses every other state's already-good work "
             "for that run too.",
    )
    return parser.parse_args()


# ── Main run ──────────────────────────────────────────────────

def run(dry_run=False, max_articles=None, no_alerts=False, skip_ucc=False, gmail_days_back=None, gmail_only=False, ucc_states=None, ucc_only=False):
    mode = "DRY RUN" if dry_run else "LIVE"
    logger.info(f"=== Nursing Home Acquisition Pipeline Starting [{mode}] ===")
    health.reset()

    conn = psycopg2.connect(config.database_url)
    psycopg2.extras.register_uuid()

    try:
        # Step 1 — Discover new articles
        articles = discover_articles(conn, skip_ucc=skip_ucc, gmail_days_back=gmail_days_back, gmail_only=gmail_only, ucc_states=ucc_states, ucc_only=ucc_only)
        total_found = len(articles)
        logger.info(f"Discovered {total_found} new articles")

        # UCC filings and CHOW records are both fast (no Claude call, "pre_extracted"
        # or cap-exempt UCC) — exempt from the per-run cap so they drain in a single
        # run regardless of count. The cap only limits Claude-extraction articles,
        # since it exists to bound Claude cost per run, not article volume. (Found
        # 2026-09-22: pre_extracted CHOW articles used to get sliced by this cap
        # alongside real text articles even though they don't call Claude either --
        # combined with chow_seen_records marking every found row as seen regardless
        # of whether it actually got processed, this silently and permanently
        # dropped whatever CHOW records fell past the cap on a given run.)
        ucc_articles    = [a for a in articles if a.get("ucc_filing")]
        pre_extracted   = [a for a in articles if not a.get("ucc_filing") and a.get("pre_extracted")]
        text_articles   = [a for a in articles if not a.get("ucc_filing") and not a.get("pre_extracted")]

        cap = max_articles or config.max_articles_per_run
        if len(text_articles) > cap:
            logger.warning(
                f"Text article count ({len(text_articles)}) exceeds cap ({cap}) — "
                f"processing first {cap} only. "
                f"Estimated Claude cost for full batch: {estimate_cost(len(text_articles))}"
            )
            text_articles = text_articles[:cap]
            health.note(f"text article cap hit: processed {cap} of {len(text_articles)}+")

        logger.info(
            f"Processing {len(ucc_articles)} UCC (cap-exempt) + "
            f"{len(pre_extracted)} CHOW/CON pre-extracted + "
            f"{len(text_articles)} text articles (async) — "
            f"estimated Claude cost: {estimate_cost(len(text_articles))}"
        )

        if dry_run:
            logger.info("[DRY RUN] Would process the following articles:")
            for a in ucc_articles + pre_extracted + text_articles:
                logger.info(f"  {a.get('title', a['url'])}")
            logger.info("[DRY RUN] No data written to database")
            return

        new_deals = 0

        # Step 2a — UCC filings (fast path, serial, no Claude)
        for i, article in enumerate(ucc_articles, 1):
            logger.info(f"UCC {i}/{len(ucc_articles)}: {article.get('title', article['url'])[:80]}")
            new_deals += process_article(article, conn)
            conn.commit()

        # Step 2a.5 — CMS/UCC relink: check every unconfirmed deal against
        # the ucc_filings audit table (entirely DB-side, no portal hits).
        # A standard step after any UCC-touching run, scoped to just the
        # state(s) this run covered (see scripts/relink_cms_ucc.py).
        if not skip_ucc and not gmail_only:
            try:
                from scripts.relink_cms_ucc import relink_cms_ucc
                relinked = relink_cms_ucc(conn, states=ucc_states, verbose=False)
                if relinked:
                    logger.info(f"CMS/UCC relink: {relinked} previously-unconfirmed deals now corroborated")
            except Exception as e:
                logger.warning(f"CMS/UCC relink failed: {e}")
                health.source_failed("CMS/UCC relink", e)

            # Step 2a.6 — name the facility behind each new UCC debtor
            # (CMS exact match, then CHOW buyer -> facility). Without this a
            # UCC deal's facility_names is just the debtor copied over.
            try:
                from scripts.enrich_ucc_facility_names import run as enrich_ucc_facility_names
                enriched = enrich_ucc_facility_names(conn=conn)
                if enriched:
                    logger.info(f"UCC facility-name enrichment: {enriched} deals named")
            except Exception as e:
                logger.warning(f"UCC facility-name enrichment failed: {e}")
                health.source_failed("UCC facility-name enrichment", e)

        # Step 2b — CHOW pre-extracted (fast path, serial, no Claude)
        for article in pre_extracted:
            new_deals += process_article(article, conn)
            conn.commit()

        # Step 2c — Text articles: async parallel fetch+Claude, serial DB writes
        if text_articles:
            logger.info(f"Starting async extraction for {len(text_articles)} text articles...")
            results = asyncio.run(_batch_fetch_extract(text_articles, concurrency=8))
            for article, raw_text, deals in results:
                logger.info(f"Storing: {article.get('title', article['url'])[:80]}")
                new_deals += _store_article_result(article, raw_text, deals, conn)
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

        log_source_health(conn)
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

def discover_articles(conn, skip_ucc: bool = False, gmail_days_back: int = None, gmail_only: bool = False, ucc_states: list[str] = None, ucc_only: bool = False) -> list[dict]:
    if ucc_only:
        return _discover_ucc(conn, [], ucc_states, ucc_only=True)
    new_articles = []

    if not gmail_only:
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
        chow_deals = fetch_chow_deals(conn)
        for deal in chow_deals:
            if not _article_exists(deal["url"], conn):
                deal["source_id"] = chow_source_id
                new_articles.append(deal)

        # CMS Ownership file — monthly, ~1-2 months after closing. Reloads
        # the file when the stored copy is stale, then turns new owners into
        # deals (see scraper/cms_owner_changes.py)
        try:
            refresh_ownership_if_stale(conn)
            cms_owner_source_id = ensure_cms_owner_change_source(conn)
            for deal in fetch_cms_owner_change_deals(conn):
                if not _article_exists(deal["url"], conn):
                    deal["source_id"] = cms_owner_source_id
                    new_articles.append(deal)
        except Exception as e:
            conn.rollback()
            logger.warning(f"CMS ownership changes skipped: {e}")
            health.source_failed("CMS ownership changes", e)

        # State CON / ownership-change notices — pre-closing filings. Nursing
        # home notices already carry raw_text, so they take the normal Claude
        # path; other facility types are stored now so their PDFs are never
        # downloaded again.
        con_al_source_id = _ensure_source(
            type("S", (), {"name": CON_AL_SOURCE_NAME,
                           "url": CON_AL_INDEX_URL,
                           "source_type": "con"})(),
            conn
        )
        con_notices, con_skipped = fetch_con_al_notices(lambda url: _article_exists(url, conn))
        for art in con_skipped:
            art["source_id"] = con_al_source_id
            _mark_extraction_error(_store_article(art, conn), art["skip_reason"], conn)
        conn.commit()
        for art in con_notices:
            art["source_id"] = con_al_source_id
            new_articles.append(art)

        con_ok_source_id = _ensure_source(
            type("S", (), {"name": CON_OK_SOURCE_NAME,
                           "url": CON_OK_INDEX_URL,
                           "source_type": "con"})(),
            conn
        )
        for deal in fetch_con_ok_deals(lambda url: _article_exists(url, conn)):
            deal["source_id"] = con_ok_source_id
            new_articles.append(deal)

        con_me_source_id = _ensure_source(
            type("S", (), {"name": CON_ME_SOURCE_NAME,
                           "url": CON_ME_INDEX_URL,
                           "source_type": "con"})(),
            conn
        )
        for art in fetch_con_me_cases(lambda url: _article_exists(url, conn)):
            art["source_id"] = con_me_source_id
            new_articles.append(art)

        con_mi_source_id = _ensure_source(
            type("S", (), {"name": CON_MI_SOURCE_NAME,
                           "url": CON_MI_INDEX_URL,
                           "source_type": "con"})(),
            conn
        )
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT upper(provider_city) FROM cms_facilities WHERE provider_state = 'MI'")
            mi_cities = {row[0] for row in cur.fetchall() if row[0]}
        for deal in fetch_con_mi_deals(lambda url: _article_exists(url, conn), mi_cities):
            deal["source_id"] = con_mi_source_id
            new_articles.append(deal)

        con_ms_source_id = _ensure_source(
            type("S", (), {"name": CON_MS_SOURCE_NAME,
                           "url": CON_MS_INDEX_URL,
                           "source_type": "con"})(),
            conn
        )
        # Weekly items drop off 30 days after completion: backfill the whole
        # recency window once, then only the latest weeks
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM articles WHERE source_id = %s)", (con_ms_source_id,))
            ms_backfill = not cur.fetchone()[0]
        for deal in fetch_con_ms_deals(lambda url: _article_exists(url, conn), backfill=ms_backfill):
            deal["source_id"] = con_ms_source_id
            new_articles.append(deal)

        con_nc_source_id = _ensure_source(
            type("S", (), {"name": CON_NC_SOURCE_NAME,
                           "url": CON_NC_INDEX_URL,
                           "source_type": "con"})(),
            conn
        )
        with conn.cursor() as cur:
            cur.execute("SELECT provider_name FROM cms_facilities WHERE provider_state = 'NC'")
            nc_cms_names = [row[0] for row in cur.fetchall() if row[0]]
        for deal in fetch_con_nc_deals(lambda url: _article_exists(url, conn), nc_cms_names):
            deal["source_id"] = con_nc_source_id
            new_articles.append(deal)

        con_md_source_id = _ensure_source(
            type("S", (), {"name": CON_MD_SOURCE_NAME,
                           "url": CON_MD_INDEX_URL,
                           "source_type": "con"})(),
            conn
        )
        for art in fetch_con_md_cases(lambda url: _article_exists(url, conn)):
            art["source_id"] = con_md_source_id
            new_articles.append(art)

        con_nj_source_id = _ensure_source(
            type("S", (), {"name": CON_NJ_SOURCE_NAME,
                           "url": CON_NJ_OPERATOR_URL,
                           "source_type": "con"})(),
            conn
        )
        for art in fetch_con_nj(lambda url: _article_exists(url, conn)):
            art["source_id"] = con_nj_source_id
            new_articles.append(art)

    # Gmail alerts — Google Alert emails sent to dedicated inbox
    try:
        gmail_source_url = "gmail://googlealerts-noreply@google.com"
        with conn.cursor() as cur:
            cur.execute("SELECT last_fetched_at FROM sources WHERE url = %s", (gmail_source_url,))
            row = cur.fetchone()
        # Widen the lookback to cover any gap since the last run (e.g. cron
        # missed a day) — capped so a long-dead pipeline doesn't pull an
        # unbounded backlog in one shot. Explicit --gmail-days-back always wins
        # (e.g. for manual backfill when the tracked timestamp itself is stale).
        if gmail_days_back is not None:
            effective_days_back = gmail_days_back
        else:
            effective_days_back = 2
            if row and row[0]:
                days_since_last_fetch = (datetime.now(timezone.utc) - row[0]).days + 1
                effective_days_back = max(2, min(days_since_last_fetch, 30))
        if effective_days_back > 2:
            logger.info(f"Gmail alerts: widening lookback to {effective_days_back} days")

        gmail_source_id = _ensure_source(
            type("S", (), {"name": "Google Alerts (Gmail)",
                           "url": gmail_source_url,
                           "source_type": "rss"})(),
            conn
        )
        alert_articles = fetch_alert_articles(days_back=effective_days_back)
        for art in alert_articles:
            if not _article_exists(art["url"], conn):
                art["source_id"] = gmail_source_id
                new_articles.append(art)
        logger.info(f"Gmail alerts: {len(alert_articles)} articles found")
    except Exception as e:
        logger.warning(f"Gmail alerts skipped: {e}")
        health.source_failed("Gmail alerts", e)

    if gmail_only:
        logger.info("RSS/EDGAR/CHOW/UCC skipped (--gmail-only)")
        return new_articles

    # UCC-1 financing statements — state-level early acquisition signal,
    # runs as both confirmation of existing deals and a new source (see
    # process_article's ucc_filing branch for the routing logic)
    if skip_ucc:
        logger.info("UCC filing fetch skipped (--skip-ucc)")
        return new_articles
    return _discover_ucc(conn, new_articles, ucc_states)


def _discover_ucc(conn, new_articles: list[dict], ucc_states: list[str] = None, ucc_only: bool = False) -> list[dict]:
    try:
        ucc_source_id = ensure_ucc_source(conn)
        if ucc_only:
            # Release the sources-row lock before the long scrape so a
            # parallel --ucc-only run for another state isn't blocked on it
            conn.commit()
        wanted = {s.upper() for s in ucc_states} if ucc_states else None
        known_operator_names = _get_known_operator_names(conn)
        ky_names = get_chow_operator_names("KY") if wanted is None or "KY" in wanted else None
        oh_names = get_chow_operator_names("OH") if wanted is None or "OH" in wanted else None
        ny_names = get_chow_operator_names("NY") if wanted is None or "NY" in wanted else None
        nj_names = get_chow_operator_names("NJ") if wanted is None or "NJ" in wanted else None
        ca_names, ca_offset = None, 0
        if wanted is None or "CA" in wanted:
            ca_names, ca_offset = _rotate_ca_names(
                conn, get_chow_operator_names("CA") + _get_known_operator_names(conn, "CA"))
        ny_individual_names = _get_cms_individual_owner_names(conn, "NY") if wanted is None or "NY" in wanted else None
        oh_individual_names = _get_cms_individual_owner_names(conn, "OH") if wanted is None or "OH" in wanted else None
        ucc_articles = fetch_ucc_filings(
            known_operator_names=known_operator_names,
            ky_bulk_file_path=getattr(config, "ky_ucc_bulk_file_path", None),
            ky_search_names=ky_names or None,
            ny_search_names=ny_names or None,
            ny_individual_names=ny_individual_names or None,
            oh_search_names=oh_names or None,
            oh_individual_names=oh_individual_names or None,
            nj_search_names=nj_names or None,
            ca_search_names=ca_names or None,
            states=ucc_states,
        )
        if ca_names:
            _save_ca_offset(conn, ca_offset, len(ca_names))
        for art in ucc_articles:
            if not _article_exists(art["url"], conn):
                art["source_id"] = ucc_source_id
                new_articles.append(art)
        logger.info(f"UCC filings: {len(ucc_articles)} articles found")
    except Exception as e:
        logger.warning(f"UCC filing fetch skipped: {e}")
        health.source_failed("UCC", e)

    return new_articles


# ── Article processing ────────────────────────────────────────

def _fetch_and_extract(article: dict) -> tuple[dict, str | None, list[dict]]:
    """Fetch article text and call Claude. No DB operations — safe to run in a thread."""
    raw_text = article.get("raw_text")
    if not raw_text:
        health.attempted("Article text fetch")
        raw_text = fetch_article_text(article["url"])
        if not raw_text:
            health.failed("Article text fetch", article["url"])
    if not raw_text:
        return article, None, []
    health.attempted("Claude extraction")
    try:
        deals = extract_deals(raw_text, article["url"], article.get("published_at"))
    except Exception as e:
        logger.error(f"Extraction failed for {article['url']}: {e}")
        health.failed("Claude extraction", f"{article['url']}: {e}")
        return article, raw_text, []
    return article, raw_text, deals


async def _batch_fetch_extract(
    articles: list[dict],
    concurrency: int = 8,
) -> list[tuple[dict, str | None, list[dict]]]:
    """Fetch+extract multiple articles concurrently using a thread-pool semaphore.
    asyncio.to_thread keeps psycopg2 (not async-safe) out of threads; DB writes
    happen serially in the caller after this returns."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(article: dict):
        async with sem:
            return await asyncio.to_thread(_fetch_and_extract, article)

    return await asyncio.gather(*[_one(a) for a in articles])


def _default_con_dates(article: dict, deals: list[dict]) -> list[dict]:
    """A CON filing that states no closing date is still dated by the filing
    itself; without this the site shows the day we ingested it instead."""
    if article.get("source_type") == "con" and article.get("published_at"):
        for d in deals:
            if not d.get("acquisition_date"):
                d["acquisition_date"] = str(article["published_at"])[:10]
    return deals


def _store_article_result(article: dict, raw_text: str | None, deals: list[dict], conn) -> int:
    """DB-write half of the text-article path — called after async extraction."""
    article_id = _store_article(article, conn)
    if not raw_text:
        _mark_extraction_error(article_id, "No article text available", conn)
        return 0
    _update_article_text(article_id, raw_text, conn)
    if not deals:
        _mark_extraction_done(article_id, conn)
        return 0
    deals = deduplicate_batch(_default_con_dates(article, deals))
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
            # CON filings are already confirmed nursing-facility filings at the
            # source; the name-based AL/MC check would dismiss a mixed portfolio
            # over one "...Assisted Living" facility (Eagle Arc/Links, ME)
            if article.get("source_type") != "con" and is_out_of_scope(deal):
                logger.info(
                    f"Auto-dismissing out-of-scope AL/MC deal: "
                    f"{deal.get('acquiring_entity')} / {deal.get('operator_names')} "
                    f"/ {deal.get('facility_names')}"
                )
                with conn.cursor() as sc:
                    sc.execute("UPDATE deals SET stage = 'dismissed' WHERE id = %s", (deal_id,))
            else:
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
            "ccn", "ccns", "_con_id", "_cms_change_id",
        ] if k in article}
        deal["extraction_model"] = article.get("extraction_model", "chow_direct")
        deals = [deal]
        deals = deduplicate_batch(deals)
        stored = 0
        for d in deals:
            hash_val = d.get("dedup_hash") or make_dedup_hash(d)
            if is_duplicate(hash_val, conn):
                continue
            # CON records can name a buyer (MI "... BY X OPCO LLC"), which
            # brings the semantic-dedup unique index into play; a collision
            # must skip this record, not abort the whole run
            with conn.cursor() as sp:
                sp.execute("SAVEPOINT before_deal")
            try:
                deal_id = _store_deal(d, article_id, conn)
            except psycopg2.errors.UniqueViolation:
                with conn.cursor() as sp:
                    sp.execute("ROLLBACK TO SAVEPOINT before_deal")
                logger.debug(f"Semantic duplicate skipped: {d.get('acquiring_entity')} {d.get('states')}")
                continue
            _run_cms_matching(d, deal_id, conn)
            stored += 1
        _mark_extraction_done(article_id, conn)
        return stored

    raw_text = article.get("raw_text")
    if not raw_text:
        health.attempted("Article text fetch")
        raw_text = fetch_article_text(article["url"])
        if raw_text:
            _update_article_text(article_id, raw_text, conn)
        else:
            health.failed("Article text fetch", article["url"])

    if not raw_text:
        _mark_extraction_error(article_id, "No article text available", conn)
        return 0

    health.attempted("Claude extraction")
    try:
        deals = extract_deals(raw_text, article["url"], article.get("published_at"))
    except Exception as e:
        logger.error(f"Extraction failed for {article['url']}: {e}")
        health.failed("Claude extraction", f"{article['url']}: {e}")
        _mark_extraction_error(article_id, str(e), conn)
        return 0

    if not deals:
        _mark_extraction_done(article_id, conn)
        return 0

    deals = deduplicate_batch(_default_con_dates(article, deals))

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
            # CON filings are already confirmed nursing-facility filings at the
            # source; the name-based AL/MC check would dismiss a mixed portfolio
            # over one "...Assisted Living" facility (Eagle Arc/Links, ME)
            if article.get("source_type") != "con" and is_out_of_scope(deal):
                logger.info(
                    f"Auto-dismissing out-of-scope AL/MC deal: "
                    f"{deal.get('acquiring_entity')} / {deal.get('operator_names')} "
                    f"/ {deal.get('facility_names')}"
                )
                with conn.cursor() as sc:
                    sc.execute("UPDATE deals SET stage = 'dismissed' WHERE id = %s", (deal_id,))
            else:
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


def _build_known_ccn_match(deal: dict, conn) -> list[dict]:
    """CHOW deals arrive with a CMS-verified CCN already known from the
    filing itself (CCN - BUYER column), and CMS Ownership-file changes
    with one per facility (ccns) — fuzzy-matching against
    cms_ownership_records would be an approximation of something we
    already have exactly, so build the match records directly instead."""
    ccns = deal.get("ccns") or [deal["ccn"]]
    method = "cms_ownership_direct" if deal.get("ccns") else "chow_ccn_direct"
    with conn.cursor() as cur:
        cur.execute("SELECT ccn, provider_name, provider_state FROM cms_facilities WHERE ccn = ANY(%s)", (ccns,))
        facilities = {row[0]: row[1:] for row in cur.fetchall()}
    return [{
        "ccn":                  ccn,
        "provider_name":        facilities.get(ccn, (None, None))[0],
        "owner_name":           deal.get("acquiring_entity"),
        "owner_type":           None,
        "provider_state":       facilities.get(ccn, (None, None))[1] or (deal.get("states") or [None])[0],
        "ownership_start_date": deal.get("acquisition_date"),
        "match_score":          100,
        "match_method":         method,
        "matched_on_field":     "ccn",
    } for ccn in ccns]


# A CMS ownership record older than this before a state filing belongs to the
# owner who is selling, so it can't confirm the sale
CON_CONFIRM_LOOKBACK_DAYS = 30


def _con_confirming_matches(deal_id, matches: list[dict], conn) -> list[dict]:
    """
    For a deal from a state pre-closing filing, only CMS records that start
    around or after the filing can confirm it. Anything older is the seller
    (audit 2026-09-30: 7 of 28 "confirmed" CON deals rested only on pre-filing
    owner/staff records, e.g. Ridgeway AL matched a 2025-11 owner for a
    2026-03 filing). Other deals' matches pass through unchanged.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.published_at::date FROM deals d
            JOIN articles a ON a.id = d.article_id JOIN sources s ON s.id = a.source_id
            WHERE d.id = %s AND s.source_type = 'con'
        """, (deal_id,))
        row = cur.fetchone()
    if not row or not row[0]:
        return matches
    floor = row[0] - timedelta(days=CON_CONFIRM_LOOKBACK_DAYS)
    return [m for m in matches
            if m.get("ownership_start_date") and str(m["ownership_start_date"])[:10] >= floor.isoformat()]


def _run_cms_matching(deal: dict, deal_id, conn):
    if deal.get("ccn") or deal.get("ccns"):
        matches = _build_known_ccn_match(deal, conn)
    else:
        matches = match_deal(deal, conn)
        if deal.get("extraction_model") == "ucc_filing":
            matches = [m for m in matches if m["match_score"] >= config.ucc_min_match_score]
    matches = enrich_matches(matches, deal.get("states") or [], conn)
    matches = flag_policy_risks(matches)
    stage, confidence = determine_stage(_con_confirming_matches(deal_id, matches, conn))

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

def _get_known_operator_names(conn, state: str = None) -> list[str]:
    """Pull entity names (LLC, Inc, Corp etc.) from deals DB for UCC search.
    Skips personal names — Organization search won't find them anyway.
    state limits it to deals involving that state (CA, whose portal blocks
    sustained volume, can't afford the full national list)."""
    import re
    entity_pattern = re.compile(
        r'\b(LLC|INC|CORP|LTD|LP|LLP|HOLDINGS|GROUP|CARE|HEALTH|MANAGEMENT|'
        r'ASSOCIATES|SERVICES|CENTER|PARTNERS|TRUST|ACQUISITION|OPERATING)\b',
        re.IGNORECASE
    )
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT name FROM (
                SELECT unnest(operator_names) AS name FROM deals
                WHERE operator_names IS NOT NULL AND (%(state)s IS NULL OR %(state)s = ANY(states))
                UNION
                SELECT acquiring_entity AS name FROM deals
                WHERE acquiring_entity IS NOT NULL AND (%(state)s IS NULL OR %(state)s = ANY(states))
            ) t
        """, {"state": state})
        all_names = [row[0] for row in cur.fetchall() if row[0]]
    return [n for n in all_names if entity_pattern.search(n)]


CA_OFFSET_KEY = "ucc_ca_next_offset"


def _rotate_ca_names(conn, names: list[str]) -> tuple[list[str], int]:
    """CA's portal blocks a run partway through (after ~160 of ~440 names on
    2026-09-30), so each run starts where the last one stopped: sort the
    names for a stable order, then rotate to the saved offset."""
    from cms.fetch_cms import _get_checkpoint
    names = sorted({n.strip() for n in names if n and n.strip()}, key=str.upper)
    if not names:
        return names, 0
    offset = _get_checkpoint(conn, CA_OFFSET_KEY) % len(names)
    logger.info(f"CA UCC: {len(names)} names, starting at #{offset} ({names[offset]})")
    return names[offset:] + names[:offset], offset


def _save_ca_offset(conn, offset: int, total: int) -> None:
    from cms.fetch_cms import _save_checkpoint
    from ucc import ca_playwright
    searched = ca_playwright.LAST_SEARCHED
    _save_checkpoint(conn, CA_OFFSET_KEY, (offset + searched) % total)
    conn.commit()
    logger.info(f"CA UCC: searched {searched} of {total} names; next run starts at #{(offset + searched) % total}")


# Ownership/control roles only — excludes weaker-signal roles (ADP of the
# SNF, W-2 managing employee, corporate director/officer, trustee) that are
# mostly long-tenured administrators rather than beneficial owners. Each
# name here becomes one full NY UCC portal search (~6-10s, own browser
# session per ucc/ny_playwright.py), so narrow this further if runtime
# becomes a problem — e.g. drop OPERATIONAL/MANAGERIAL CONTROL and
# MANAGING CONTROL - GOVERNING BODY to keep only equity-ownership roles.
_CMS_OWNERSHIP_RELEVANT_ROLES = (
    '5% OR GREATER DIRECT OWNERSHIP INTEREST',
    '5% OR GREATER INDIRECT OWNERSHIP INTEREST',
    'DIRECT OWNERSHIP INTEREST',
    'INDIRECT OWNERSHIP INTEREST',
    'GENERAL PARTNERSHIP INTEREST',
    'LIMITED PARTNERSHIP INTEREST',
    'OPERATIONAL/MANAGERIAL CONTROL',
    'MANAGING CONTROL - GOVERNING BODY',
)


def _get_cms_individual_owner_names(conn, state: str) -> list[str]:
    """Pull individual (non-org) CMS owner names for a state, restricted to
    ownership/control roles — Tyler's methodology: match CMS owner names
    (including individuals, which CHOW/deals-derived names never capture)
    against the UCC filing index."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT owner_name FROM cms_ownership_records
            WHERE provider_state = %s
              AND owner_type = 'Individual'
              AND owner_role = ANY(%s)
        """, (state, list(_CMS_OWNERSHIP_RELEVANT_ROLES)))
        return [row[0] for row in cur.fetchall() if row[0]]


def _fetch_existing_deals_for_ucc_matching(conn, states: list[str] = None) -> list[ExistingDeal]:
    """states scopes the query to just deals overlapping those states --
    important beyond correctness: this runs once per filing (called from
    _process_ucc_filing), so an unscoped --ucc-states NY run was
    re-fetching and reconstructing every deal in the entire table
    (1300+, all states) for every single NY filing processed."""
    with conn.cursor() as cur:
        if states:
            cur.execute("""
                SELECT id, operator_names, facility_names, acquisition_date, lender, states
                FROM deals
                WHERE states && %s
            """, ([s.upper() for s in states],))
        else:
            cur.execute("""
                SELECT id, operator_names, facility_names, acquisition_date, lender, states
                FROM deals
            """)
        return [
            ExistingDeal(
                id=row[0], operator_names=row[1] or [], facility_names=row[2] or [],
                acquisition_date=row[3], lender=row[4], states=row[5] or [],
            )
            for row in cur.fetchall()
        ]


def _process_ucc_filing(article: dict, article_id, conn) -> int:
    filing = article["_ucc_filing_obj"]
    classification = article["_ucc_classification"]

    # Audit log of every filing seen, regardless of relevance/routing outcome
    # below -- also the only place detail_url (real per-filing deep links,
    # currently NY only) gets persisted. save_ucc_filings previously existed
    # but was never actually called anywhere in the live pipeline.
    try:
        save_ucc_filings([filing], conn)
    except Exception as e:
        logger.warning(f"save_ucc_filings failed for {filing.state}/{filing.filing_number}: {e}")
        health.failed("UCC filing save", f"{filing.state}/{filing.filing_number}: {e}")

    if not classification.is_acquisition_relevant:
        logger.debug(
            f"UCC filing excluded (not RE/PE relevant): "
            f"{filing.secured_party_name} [{classification.category.value}]"
        )
        _mark_extraction_done(article_id, conn)
        return 0

    existing_deals = _fetch_existing_deals_for_ucc_matching(conn, states=[filing.state])
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
        # NJ's non-certified search never returns a secured-party name (a
        # paid per-filing lookup would be needed) -- that's a known,
        # permanent source gap, not a sign the filing itself is
        # untrustworthy, so it's let through here unlike every other state
        # (where an empty secured-party name usually means a scraper
        # glitch worth staying cautious about). Its lender field below
        # gets an explicit placeholder instead of staying blank so a
        # viewer can tell at a glance that lender verification wasn't
        # possible for this deal, not just silently omitted.
        if not filing.secured_party_name and filing.state != "NJ":
            logger.debug(
                f"UCC new-signal skipped: no secured party "
                f"(filing {filing.filing_number}, {filing.state})"
            )
            _mark_extraction_done(article_id, conn)
            return 0

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
            "lender": filing.secured_party_name or (
                "Not available — NJ UCC search doesn't return lender names"
                if filing.state == "NJ" else None
            ),
            "extraction_model": "ucc_filing",
            "_ucc_filing_number": filing.filing_number,
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
                   acquisition_date, recheck_count, extraction_model
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

def ensure_ucc_source(conn) -> str:
    """The single `sources` row every UCC-1 article hangs off. Shared with
    scripts/ingest_manual_ucc.py -- an article without it has a NULL
    source_type, so the frontend shows it as "News" and export_deals.py
    can't turn its ucc:// URL into a real link."""
    return _ensure_source(
        type("S", (), {"name": "State UCC-1 Filings",
                       "url": "ucc://multi-state",
                       "source_type": "ucc"})(),
        conn
    )


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
    if url in EXCLUDED_URLS:
        return True
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    if domain in EXCLUDED_DOMAINS:
        return True
    if any(pattern in url for pattern in EXCLUDED_PATTERNS):
        return True
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
        deal_id = cur.fetchone()[0]
    return find_and_resolve_fuzzy_duplicate(deal_id, conn)


def _store_cms_matches(deal_id, matches: list[dict], conn):
    with conn.cursor() as cur:
        # keep scripts/enrich_ucc_facility_names.py's rows: a recheck
        # doesn't reproduce them, and enrichment won't redo a deal whose
        # facility name it already set
        cur.execute("""
            DELETE FROM cms_matches WHERE deal_id = %s AND match_method NOT LIKE 'ucc_debtor%%'
        """, (deal_id,))
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
            skip_ucc=args.skip_ucc,
            gmail_days_back=args.gmail_days_back,
            gmail_only=args.gmail_only,
            ucc_states=[s.strip() for s in args.ucc_states.split(",") if s.strip()] if args.ucc_states else None,
            ucc_only=args.ucc_only,
        )
        # non-zero when a source or step failed -- data that did succeed is
        # already committed; see pipeline/run_health.py
        sys.exit(health.report())
