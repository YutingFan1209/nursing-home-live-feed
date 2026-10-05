"""
RSS feed scraper.
Fetches and parses RSS feeds, returns new articles not yet in DB.
"""

import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional
import feedparser
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import get_config
from pipeline.run_health import health

logger = logging.getLogger(__name__)
config = get_config()

HEADERS = {
    "User-Agent": (
        "NursingHomeAcquisitionTracker/1.0 "
        "(health policy research; contact: research@yourorg.org)"
    )
}


def _http_get(url: str, timeout: int):
    """
    requests.get, retried with a Chrome-impersonating client on 403.

    McKnight's sits behind Cloudflare bot protection that 403s every plain
    HTTP client -- feed, homepage and articles, whatever the User-Agent --
    because it fingerprints the TLS handshake, not the headers. Its feed had
    never stored an article (found 2026-09-28). curl_cffi mimics a real
    Chrome handshake and gets through.
    """
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    if resp.status_code == 403:
        from curl_cffi import requests as cffi_requests
        logger.info(f"403 from {url}, retrying with browser impersonation")
        resp = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    resp.raise_for_status()
    return resp


def unwrap_google_redirect(url: str) -> str:
    """Google Alerts feed links are https://www.google.com/url?...&url=<real
    article>&...; fetching the wrapper never yields the article (all 51
    alert-feed articles from 2026-09 failed with "No article text"), and
    the wrapped URL also defeats URL dedup against the Gmail alert copy."""
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        target = parse_qs(parsed.query).get("url") or parse_qs(parsed.query).get("q")
        if target:
            return target[0]
    return url


def _strip_tags(text: str) -> str:
    import re
    import html
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def fetch_feed(url: str) -> list[dict]:
    logger.info(f"Fetching RSS feed: {url}")
    # Fetch with requests, not feedparser's own urllib fetch: urllib uses the
    # interpreter's CA store, which this Python install doesn't have, so every
    # direct feed failed CERTIFICATE_VERIFY_FAILED and feedparser swallowed it
    # as an empty feed (found 2026-09-23 -- no direct-feed article had ever
    # been stored; all news was arriving via Gmail alerts). requests uses certifi.
    try:
        resp = _http_get(url, timeout=30)
        feed = feedparser.parse(resp.content)
    except Exception as e:
        logger.error(f"Failed to fetch feed {url}: {e}")
        health.source_failed(f"RSS feed {url}", e)
        return []
    if feed.bozo and not feed.entries:
        logger.error(f"Feed {url} returned no entries: {feed.get('bozo_exception')}")
        health.source_failed(f"RSS feed {url}", feed.get("bozo_exception"))
        return []

    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.max_article_age_days)

    for entry in feed.entries:
        try:
            published_at = _parse_date(entry)
            if published_at and published_at < cutoff:
                continue
            article_url = unwrap_google_redirect(entry.get("link", "").strip())
            if not article_url:
                continue
            # Google Alerts titles carry <b> highlighting
            title = _strip_tags(entry.get("title", ""))
            summary = _strip_tags(entry.get("summary", ""))
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


# main.py fetches articles from 8 threads at once. trafilatura shares one
# lxml HTML parser across calls, and parsing from several threads at once
# corrupts memory: the process dies with SIGABRT in lxml's _fixHtmlDictNames
# (crash 2026-09-23, the first run with enough articles to overlap). Downloads
# stay parallel; only the lxml parsing is serialized.
_LXML_LOCK = threading.Lock()


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
        if not downloaded:
            # trafilatura's own fetch fails on bot-protected sites (403)
            downloaded = _http_get(url, timeout=config.request_timeout).text
        if downloaded:
            with _LXML_LOCK:
                text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            if text and len(text.split()) >= 50:
                return text.strip()
    except ImportError as e:
        # was a silent `pass` -- trafilatura was broken this way for months
        # (missing lxml_html_clean) without anyone noticing
        logger.warning(f"trafilatura unavailable ({e}), using BS4 fallback")
    except Exception as e:
        logger.warning(f"trafilatura failed for {url}: {e}")

    # Fallback: requests + BeautifulSoup
    try:
        time.sleep(config.request_delay)
        resp = _http_get(url, timeout=config.request_timeout)
        from bs4 import BeautifulSoup
        with _LXML_LOCK:
            soup = BeautifulSoup(resp.text, "lxml")
            # Remove nav, footer, scripts
            for tag in soup(["nav", "footer", "script", "style", "aside"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
        return text[:config.article_max_chars] if text else None
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
