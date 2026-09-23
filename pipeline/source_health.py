"""
pipeline/source_health.py

Flags sources that have gone quiet. Every past outage in this pipeline was
silent -- RSS feeds failing SSL and returning "0 entries" for months, CHOW's
90-day filter matching nothing for the pipeline's whole life, UCC dedup
dropping filings as "duplicates" -- so the run logged success each time.
This compares each active source's last stored article against how often it
normally produces one and logs a warning at the end of every run.

CHOW (quarterly file) and UCC (run by hand, per state) aren't checked: long
gaps are normal for both.

    venv/bin/python3 -m pipeline.source_health
"""
import logging
from datetime import datetime, timezone

from scraper.sources import get_active_sources
from pipeline.run_health import health

logger = logging.getLogger(__name__)

# Days without a new stored article before a source counts as quiet. Only
# acquisition-related items are stored, so keep this loose for trade press.
MAX_QUIET_DAYS = {"rss": 14, "edgar": 30}


def check_source_health(conn, now: datetime = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    active = {s.url for s in get_active_sources() if s.source_type in MAX_QUIET_DAYS}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.name, s.url, s.source_type, MAX(a.created_at)
            FROM sources s LEFT JOIN articles a ON a.source_id = s.id
            WHERE s.source_type = ANY(%s)
            GROUP BY s.id
        """, (list(MAX_QUIET_DAYS),))
        rows = cur.fetchall()

    warnings = []
    for name, url, source_type, last_article in rows:
        # Gmail alerts are stored as an rss source but aren't in ALL_SOURCES
        if url not in active and not url.startswith("gmail://"):
            continue
        if last_article is None:
            warnings.append(f"{name}: has never stored an article")
            continue
        quiet_days = (now - last_article).days
        if quiet_days > MAX_QUIET_DAYS[source_type]:
            warnings.append(f"{name}: no new article in {quiet_days} days (last {last_article:%Y-%m-%d})")
    return warnings


def log_source_health(conn) -> None:
    try:
        warnings = check_source_health(conn)
    except Exception as e:
        logger.warning(f"Source health check failed: {e}")
        return
    for w in warnings:
        logger.warning(f"SOURCE HEALTH: {w}")
        # A source that has never stored anything may just never match the
        # acquisition keywords (McKnight's); one that stopped is broken.
        if "has never stored" in w:
            health.note(w)
        else:
            health.source_failed("Source went quiet", w)
    if not warnings:
        logger.info("Source health: all checked sources producing articles")


if __name__ == "__main__":
    import psycopg2
    from config import get_config

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log_source_health(psycopg2.connect(get_config().database_url))
