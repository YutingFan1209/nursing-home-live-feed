"""
Registered discovery sources.

Add new RSS feeds, EDGAR tickers, or search terms here.
The loader will pick these up automatically on next run.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Source:
    name: str
    url: str
    source_type: str   # 'rss' | 'edgar' | 'googlenews' | 'manual'
    active: bool = True
    notes: Optional[str] = None


# ── RSS feeds ────────────────────────────────────────────────
RSS_SOURCES = [
    Source(
        name="Skilled Nursing News",
        url="https://skillednursingnews.com/feed/",
        source_type="rss",
    ),
    Source(
        name="McKnight's Long-Term Care News",
        url="https://www.mcknights.com/feed/",
        source_type="rss",
    ),
    Source(
        name="Modern Healthcare — Post-Acute",
        url="https://www.modernhealthcare.com/section/post-acute-care/rss",
        source_type="rss",
    ),
    Source(
        name="Provider Magazine",
        url="https://www.providermagazine.com/feed/",
        source_type="rss",
    ),
    Source(
        name="Senior Housing News",
        url="https://seniorhousingnews.com/feed/",
        source_type="rss",
    ),
Source(
    name="Google Alerts — Nursing Home Acquisition",
    url="https://www.google.com/alerts/feeds/13149532980111734650/7438927054963187970",
    source_type="rss",
)
]

# ── SEC EDGAR — REIT tickers to watch for 8-K filings ───────
# These are the major nursing home REITs and large operators
# that file acquisition-related 8-Ks
EDGAR_TICKERS = [
    Source(
        name="Welltower (WELL)",
        url="WELL",
        source_type="edgar",
        notes="Largest SNF REIT; files 8-K on acquisitions/dispositions",
    ),
    Source(
        name="Sabra Health Care REIT (SBRA)",
        url="SBRA",
        source_type="edgar",
    ),
    Source(
        name="CareTrust REIT (CTRE)",
        url="CTRE",
        source_type="edgar",
    ),
    Source(
        name="National Health Investors (NHI)",
        url="NHI",
        source_type="edgar",
    ),
    Source(
        name="Omega Healthcare Investors (OHI)",
        url="OHI",
        source_type="edgar",
    ),
    Source(
        name="LTC Properties (LTC)",
        url="LTC",
        source_type="edgar",
    ),
]

# ── Google News / SerpAPI search queries ────────────────────
GOOGLE_NEWS_QUERIES = [
    Source(
        name="Google News — nursing home acquisition",
        url="nursing home acquisition skilled nursing",
        source_type="googlenews",
    ),
    Source(
        name="Google News — SNF portfolio deal",
        url="skilled nursing facility portfolio acquisition deal",
        source_type="googlenews",
    ),
    Source(
        name="Google News — nursing home ownership change",
        url="nursing home ownership change operator",
        source_type="googlenews",
    ),
]

from scraper.chow import CHOW_SOURCE_NAME, CHOW_SOURCE_URL

# ── CHOW source ──────────────────────────────────────────────
CHOW_SOURCES = [
    Source(
        name=CHOW_SOURCE_NAME,
        url=CHOW_SOURCE_URL,
        source_type="chow",
        notes="Quarterly CMS SNF Change of Ownership — covers ALL private + public deals",
    ),
]

# ── All sources combined ─────────────────────────────────────
ALL_SOURCES = RSS_SOURCES + EDGAR_TICKERS + GOOGLE_NEWS_QUERIES + CHOW_SOURCES


def get_active_sources(source_type: str = None) -> list[Source]:
    """Return active sources, optionally filtered by type."""
    sources = [s for s in ALL_SOURCES if s.active]
    if source_type:
        sources = [s for s in sources if s.source_type == source_type]
    return sources
