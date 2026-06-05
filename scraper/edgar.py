"""
SEC EDGAR full-text search scraper.
Uses EDGAR's EFTS (full-text search) API to find 8-K filings
mentioning nursing home acquisitions.

Two strategies:
1. Search by known REIT/operator entity names — catches their filings early
2. Broad keyword search — catches any company mentioning SNF acquisitions

The EFTS API is more reliable than the old RSS feed approach
and doesn't require pre-registering tickers.

API docs: https://efts.sec.gov/LATEST/search-index
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import get_config

logger = logging.getLogger(__name__)
config = get_config()

# SEC requires a descriptive User-Agent with contact info
_contact_email = os.environ.get("EDGAR_CONTACT_EMAIL", "research@yourorg.org")
EDGAR_HEADERS = {
    "User-Agent": f"NursingHomeAcquisitionTracker/1.0 (health policy research; {_contact_email})",
    "Accept-Encoding": "gzip, deflate",
}

# EDGAR full-text search endpoint
EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

# EDGAR filing viewer base URL
EDGAR_FILING_BASE = "https://www.sec.gov/Archives/edgar"

# Known SNF REITs and large operators to search by name
KNOWN_ENTITIES = [
    "Welltower",
    "Sabra Health Care",
    "CareTrust REIT",
    "Omega Healthcare",
    "National Health Investors",
    "LTC Properties",
    "Ensign Group",
    "Genesis Healthcare",
    "Brookdale Senior Living",
    "SavaSeniorCare",
    "ProMedica",
    "Kindred Healthcare",
    "Diversicare",
    "National HealthCare",
    "Communicare Health",
    "Trilogy Health",
    "Prestige Healthcare",
]

# Keywords that indicate acquisition-related 8-K content
ACQUISITION_KEYWORDS = [
    "skilled nursing",
    "nursing facility",
    "nursing home",
    "SNF acquisition",
    "post-acute",
]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=3, max=30),
    reraise=True,
)
def _search_efts(params: dict) -> dict:
    """Query EDGAR full-text search API."""
    time.sleep(config.request_delay)
    resp = requests.get(EFTS_URL, params=params, headers=EDGAR_HEADERS, timeout=config.request_timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_edgar_filings() -> list[dict]:
    """
    Search EDGAR for recent 8-K filings mentioning SNF acquisitions.
    Returns list of article-like dicts for the pipeline.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.max_article_age_days)
    date_from = cutoff.strftime("%Y-%m-%d")
    date_to = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    filings = []
    seen_ids = set()

    # Strategy 1 — search by known entity names
    for entity in KNOWN_ENTITIES:
        try:
            results = _search_efts({
                "q": f'"acquisition" "skilled nursing"',
                "dateRange": "custom",
                "startdt": date_from,
                "enddt": date_to,
                "forms": "8-K",
                "entity": entity,
            })
            hits = results.get("hits", {}).get("hits", [])
            logger.info(f"EDGAR search '{entity}': {len(hits)} hits")
            for hit in hits:
                filing = _parse_hit(hit)
                if filing and filing["url"] not in seen_ids:
                    seen_ids.add(filing["url"])
                    filings.append(filing)
        except Exception as e:
            logger.warning(f"EDGAR entity search failed for '{entity}': {e}")
            continue

    # Strategy 2 — broad keyword search for any company
    try:
        results = _search_efts({
            "q": '"nursing home acquisition" OR "skilled nursing facility acquisition" OR "SNF portfolio"',
            "dateRange": "custom",
            "startdt": date_from,
            "enddt": date_to,
            "forms": "8-K",
        })
        hits = results.get("hits", {}).get("hits", [])
        logger.info(f"EDGAR broad search: {len(hits)} hits")
        for hit in hits:
            filing = _parse_hit(hit)
            if filing and filing["url"] not in seen_ids:
                seen_ids.add(filing["url"])
                filings.append(filing)
    except Exception as e:
        logger.warning(f"EDGAR broad search failed: {e}")

    logger.info(f"EDGAR: found {len(filings)} unique relevant filings")
    return filings


def _parse_hit(hit: dict) -> Optional[dict]:
    """Convert an EFTS search hit into an article-like dict."""
    try:
        src = hit.get("_source", {})
        file_id = hit.get("_id", "")
        file_date = src.get("file_date", "")
        company_names = src.get("display_names", ["Unknown"])
        company = company_names[0] if company_names else "Unknown"

        if not file_id or not file_date:
            return None

        # Build filing URL from file_id
        # file_id format: "0001234567-26-000123:document.htm"
        accession = file_id.split(":")[0] if ":" in file_id else file_id
        accession_clean = accession.replace("-", "")
        cik = src.get("ciks", ["0000000000"])[0].lstrip("0") or "0"
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_clean}/{file_id.split(':')[-1]}"

        published_at = None
        if file_date:
            try:
                published_at = datetime.strptime(file_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        return {
            "url": filing_url,
            "title": f"[EDGAR 8-K] {company} — filed {file_date}",
            "published_at": published_at,
            "raw_text": None,   # fetched separately if needed
            "source_type": "edgar",
            "_accession": accession,
            "_cik": cik,
            "_file_date": file_date,
            "_company": company,
        }
    except Exception as e:
        logger.warning(f"Failed to parse EDGAR hit: {e}")
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    reraise=False,
)
def fetch_filing_text(url: str) -> Optional[str]:
    """Fetch the text of an EDGAR filing document."""
    try:
        time.sleep(config.request_delay)
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=config.request_timeout)
        resp.raise_for_status()
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove boilerplate
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)[:10000]
    except Exception as e:
        logger.warning(f"Failed to fetch filing text from {url}: {e}")
        return None
