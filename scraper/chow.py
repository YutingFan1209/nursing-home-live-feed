"""
SNF Change of Ownership (CHOW) scraper.
Downloads the latest quarterly CMS CHOW CSV and converts each new
ownership change into a deal record.

This is the most reliable source for private operator deals —
every Medicare SNF ownership change is legally required to be filed.
Coverage: ALL SNFs public and private.
Cadence: Quarterly (Jan, Apr, Jul, Oct).

Column mapping from actual CSV:
  ORGANIZATION NAME - BUYER  -> acquiring_entity
  ORGANIZATION NAME - SELLER -> seller_entity
  CCN - BUYER                -> ccn
  CHOW TYPE TEXT             -> deal_type (CHANGE OF OWNERSHIP / ACQUISITION/MERGER)
  EFFECTIVE DATE             -> acquisition_date
  ENROLLMENT STATE - BUYER   -> state
"""

import csv
import io
import logging
import requests
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential

from config import get_config

logger = logging.getLogger(__name__)
config = get_config()

# Latest quarterly CHOW files — update URL each quarter
# Format: SNF_CHOW_YYYY.MM.DD.csv
# Check: https://catalog.data.gov/dataset/skilled-nursing-facility-change-of-ownership
CHOW_URLS = [
    # Most recent first — loader tries each until one works
    "https://data.cms.gov/sites/default/files/2026-01/900cec56-f1c8-40cb-9f8a-bf54cae53b90/SNF_CHOW_2026.01.02.csv",
    "https://data.cms.gov/sites/default/files/2025-10/92b32732-ba6e-4dee-9bd5-f422b45758ba/SNF_CHOW_2025.10.01.csv",
]

CHOW_SOURCE_NAME = "CMS SNF Change of Ownership"
CHOW_SOURCE_URL  = "https://catalog.data.gov/dataset/skilled-nursing-facility-change-of-ownership"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _download_chow_csv(url: str) -> list[dict]:
    """Download and parse the CHOW CSV file."""
    logger.info(f"Downloading CHOW CSV: {url}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return list(reader)


def fetch_chow_deals(last_seen_date: str = None) -> list[dict]:
    """
    Download latest CHOW CSV and return new ownership changes as deal dicts.

    Args:
        last_seen_date: ISO date string (YYYY-MM-DD). Only return records
                        with effective date after this date. If None, returns
                        all records from the last 90 days.

    Returns:
        List of deal dicts ready for extraction pipeline.
    """
    rows = None
    for url in CHOW_URLS:
        try:
            rows = _download_chow_csv(url)
            logger.info(f"Downloaded {len(rows)} CHOW records from {url}")
            break
        except Exception as e:
            logger.warning(f"Failed to download {url}: {e}")
            continue

    if not rows:
        logger.error("Could not download any CHOW CSV file")
        return []

    # Filter to new records only
    from datetime import date, timedelta
    if last_seen_date:
        cutoff = date.fromisoformat(last_seen_date)
    else:
        cutoff = date.today() - timedelta(days=90)

    deals = []
    seen_ccns = set()

    for row in rows:
        if not isinstance(row, dict):
            continue

        effective_date_str = row.get("EFFECTIVE DATE", "").strip()
        if not effective_date_str:
            continue

        try:
            effective_date = datetime.strptime(effective_date_str, "%m/%d/%Y").date()
        except ValueError:
            continue

        if effective_date <= cutoff:
            continue

        buyer   = row.get("ORGANIZATION NAME - BUYER", "").strip()
        seller  = row.get("ORGANIZATION NAME - SELLER", "").strip()
        ccn     = row.get("CCN - BUYER", "").strip()
        state   = row.get("ENROLLMENT STATE - BUYER", "").strip()
        chow_type = row.get("CHOW TYPE TEXT", "CHANGE OF OWNERSHIP").strip()

        if not buyer or not ccn:
            continue

        # Deduplicate within this batch by CCN + date
        key = f"{ccn}_{effective_date_str}"
        if key in seen_ccns:
            continue
        seen_ccns.add(key)

        # Build a synthetic article-like dict for the pipeline
        title = f"[CHOW] {buyer} acquires {seller or 'facility'} (CCN: {ccn})"
        deal = {
            # Article metadata
            "url": f"{CHOW_SOURCE_URL}#ccn-{ccn}-{effective_date_str.replace('/', '-')}",
            "title": title,
            "published_at": datetime.combine(effective_date, datetime.min.time()).replace(tzinfo=timezone.utc),
            "raw_text": _build_synthetic_text(buyer, seller, ccn, state, effective_date_str, chow_type),
            "source_name": CHOW_SOURCE_NAME,
            "source_url": CHOW_SOURCE_URL,
            "source_type": "chow",
            # Pre-extracted deal fields — skip Claude extraction
            "pre_extracted": True,
            "acquiring_entity": buyer,
            "seller_entity": seller or None,
            "operator_names": [buyer],
            "facility_names": [],
            "states": [state] if state else [],
            "facility_count": 1,
            "deal_value_m": None,
            "acquisition_date": effective_date.isoformat(),
            "financing_amount_m": None,
            "lender": None,
            "rationale": f"CMS-verified {chow_type.lower()} effective {effective_date_str}. Buyer: {buyer}. Seller: {seller}. CCN: {ccn}.",
            # CMS data we already have
            "ccn": ccn,
        }
        deals.append(deal)

    logger.info(f"Found {len(deals)} new CHOW records after {cutoff}")
    return deals


def _build_synthetic_text(buyer, seller, ccn, state, date_str, chow_type) -> str:
    """
    Build a synthetic article text from CHOW fields.
    Used as raw_text in the article record for audit trail purposes.
    """
    return (
        f"CMS Skilled Nursing Facility Change of Ownership Record\n\n"
        f"Type: {chow_type}\n"
        f"Effective Date: {date_str}\n"
        f"Buyer (New Owner): {buyer}\n"
        f"Seller (Prior Owner): {seller}\n"
        f"CMS Certification Number (CCN): {ccn}\n"
        f"State: {state}\n\n"
        f"Source: CMS Provider Enrollment - SNF Change of Ownership dataset. "
        f"This record represents a legally filed ownership change submitted to CMS."
    )


def get_chow_source_id(conn) -> str:
    """Ensure the CHOW source exists in the sources table and return its ID."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sources (name, url, source_type)
            VALUES (%s, %s, 'chow')
            ON CONFLICT (url) DO UPDATE SET last_fetched_at = NOW()
            RETURNING id
        """, (CHOW_SOURCE_NAME, CHOW_SOURCE_URL))
        return cur.fetchone()[0]
