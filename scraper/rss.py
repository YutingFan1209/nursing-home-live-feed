"""
RSS feed scraper.
Fetches and parses RSS feeds, returns new articles not yet in DB.
"""

import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import feedparser
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import get_config

logger = logging.getLogger(__name__)
config = get_config()

HEADERS = {
    "User-Agent": (
        "NursingHomeAcquisitionTracker/1.0 "
        "(health policy research; contact: research@yourorg.org)"
    )
}


def fetch_feed(url: str) -> list[dict]:
    logger.info(f"Fetching RSS feed: {url}")
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
    except Exception as e:
        logger.error(f"Failed to parse feed {url}: {e}")
        return []

    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.max_article_age_days)

    for entry in feed.entries:
        try:
            published_at = _parse_date(entry)
            if published_at and published_at < cutoff:
                continue
            article_url = entry.get("link", "").strip()
            if not article_url:
                continue
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            # Skip keyword filter for Google Alerts — Google already matched relevance
            is_google_alert = 'google.com/alerts' in url
            if not is_google_alert and not _is_acquisition_related(title + " " + summary):
                continue
            articles.append({
                "url": article_url,
                "title": title,
                "published_at": published_at,
                "raw_text": None,
            })
        except Exception as e:
            logger.warning(f"Skipping entry in {url}: {e}")
            continue

    logger.info(f"Found {len(articles)} acquisition-related articles in {url}")
    return articles


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    reraise=False,
)
def fetch_article_text(url: str) -> Optional[str]:
    """
    Fetch and parse full article text from URL.
    Uses trafilatura (actively maintained) instead of newspaper3k (abandoned).
    Falls back to requests + BeautifulSoup if trafilatura returns nothing.
    """
    try:
        import trafilatura
        time.sleep(config.request_delay)
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            if text and len(text.split()) >= 50:
                return text.strip()
    except ImportError:
        pass  # trafilatura not installed, fall through to BS4
    except Exception as e:
        logger.warning(f"trafilatura failed for {url}: {e}")

    # Fallback: requests + BeautifulSoup
    try:
        time.sleep(config.request_delay)
        resp = requests.get(url, headers=HEADERS, timeout=config.request_timeout)
        resp.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        # Remove nav, footer, scripts
        for tag in soup(["nav", "footer", "script", "style", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:10000] if text else None
    except Exception as e:
        logger.warning(f"Fallback fetch failed for {url}: {e}")
        return None


def _parse_date(entry) -> Optional[datetime]:
    """Extract published date from feed entry."""
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                continue
    return None


# Keywords that suggest an article is about acquisitions/ownership changes
ACQUISITION_KEYWORDS = [
    "acqui", "purchas", "sold", "sale", "deal", "transaction",
    "portfolio", "operator", "ownership", "financ", "invest",
    "buys", "buyer", "seller", "closes", "closing",
]

def _is_acquisition_related(text: str) -> bool:
    """Heuristic filter — only process articles about acquisitions."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in ACQUISITION_KEYWORDS)
