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
import re
import requests
from datetime import date, datetime, timedelta, timezone
from tenacity import retry, stop_after_attempt, wait_exponential

from config import get_config
from pipeline.run_health import health

logger = logging.getLogger(__name__)
config = get_config()

# Stable dataset-id discovery API (CMS's newer data.cms.gov/provider-characteristics
# platform, separate from the older provider-data/metastore API carecompare.py and
# cms/fetch_cms.py use) -- resolves to whatever CHOW CSV is currently published,
# no manual updates needed. Found 2026-09-22 after CHOW_URLS below was discovered
# stale (see its comment): confirmed via a live Chrome network-tab capture of
# https://data.cms.gov/provider-characteristics/hospitals-and-other-facilities/
# skilled-nursing-facility-change-of-ownership/data -- the dataset UUID itself
# should be stable even as CMS's frontend/URL structure evolves further.
CHOW_DATASET_ID = "1e1afa09-5699-46a5-ae9e-47017397c55c"
CHOW_RESOURCES_API = f"https://data.cms.gov/data-api/v1/dataset/{CHOW_DATASET_ID}/resources"

# Fallback if the discovery API fails — last known-good URLs, most recent first.
# Format: SNF_CHOW_YYYY.MM.DD.csv
CHOW_URLS = [
    # This list is hardcoded/manually maintained and had gone stale before
    # CHOW_RESOURCES_API above was found: as of 2026-09-22 CMS had already
    # published a 2026-07-17 file (covering the same 2016-2024 effective-date
    # window -- CHOW effective dates lag real filing time significantly, so a
    # newer release date does NOT mean newer effective dates, just
    # more/corrected historical records) that this list didn't have.
    # Confirmed 402 genuinely new records nationwide in that file vs. the one
    # below (by CCN+buyer+effective-date), including NJ+1/NY+1/KY+7/OH+21/PA+21
    # for the states this pipeline tracks. Now only reached if
    # CHOW_RESOURCES_API itself fails -- update this manually if that happens
    # and stays down, checking https://catalog.data.gov/dataset/skilled-nursing-facility-change-of-ownership.
    "https://data.cms.gov/sites/default/files/2026-10/89166aa5-0fb8-49d6-bef5-f970876794b5/SNF_CHOW_2026.09.29.csv",
    "https://data.cms.gov/sites/default/files/2026-07/cf019cb8-b8ce-45fc-a912-d1ee9a83ca1c/SNF_CHOW_2026.07.17.csv",
    "https://data.cms.gov/sites/default/files/2026-01/900cec56-f1c8-40cb-9f8a-bf54cae53b90/SNF_CHOW_2026.01.02.csv",
    "https://data.cms.gov/sites/default/files/2025-10/92b32732-ba6e-4dee-9bd5-f422b45758ba/SNF_CHOW_2025.10.01.csv",
]

CHOW_SOURCE_NAME = "CMS SNF Change of Ownership"
CHOW_SOURCE_URL  = "https://catalog.data.gov/dataset/skilled-nursing-facility-change-of-ownership"

# How far back a never-before-seen row's EFFECTIVE DATE can be and still
# become a deal candidate. Set 2026-09-22 during the seen-records backfill:
# of 5227 total rows, 609 fell within 2 years, 66 within 1 -- 2 years was
# picked as wide enough to not miss genuinely-recent-but-lagged filings
# without dragging in a decade of stale historical M&A on first activation.
CHOW_RECENCY_DAYS = 730


# CMS's site-wide catalog. CHOW_RESOURCES_API lags new releases: on 2026-10-19
# it still pointed at the 2026-07-17 file the day this catalog already listed
# SNF_CHOW_2026.09.29.csv, so both are checked and the newer file wins.
CMS_CATALOG_URL = "https://data.cms.gov/data.json"
CHOW_CATALOG_TITLE = "Skilled Nursing Facility Change of Ownership :"


def _chow_file_date(url: str) -> str:
    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\.csv$", url or "")
    return "".join(m.groups()) if m else ""


def _discover_chow_csv_url() -> str | None:
    candidates = []
    try:
        resp = requests.get(CHOW_RESOURCES_API, timeout=30)
        resp.raise_for_status()
        for resource in resp.json().get("data", []):
            if resource.get("type") == "Primary" and resource.get("file_url"):
                candidates.append(resource["file_url"])
                break
    except Exception as e:
        logger.warning(f"Discovery API lookup failed for CHOW dataset: {e}")
    try:
        resp = requests.get(CMS_CATALOG_URL, timeout=60)
        resp.raise_for_status()
        for ds in resp.json().get("dataset", []):
            # "... Change of Ownership - Owner Information : ..." is a separate dataset
            if not ds.get("title", "").startswith(CHOW_CATALOG_TITLE):
                continue
            for dist in ds.get("distribution", []):
                url = dist.get("downloadURL") or ""
                if "/SNF_CHOW_" in url and _chow_file_date(url):
                    candidates.append(url)
    except Exception as e:
        logger.warning(f"CMS catalog lookup failed for CHOW dataset: {e}")
    if not candidates:
        return None
    return max(candidates, key=_chow_file_date)


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


def _load_seen_chow_keys(conn) -> set[tuple[str, str, str]]:
    with conn.cursor() as cur:
        cur.execute("SELECT ccn, buyer_name, effective_date FROM chow_seen_records")
        return set(cur.fetchall())


def _load_home_states(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT ccn, provider_state FROM cms_facilities")
        return dict(cur.fetchall())


def _mark_chow_keys_seen(conn, keys: list[tuple[str, str, str]]) -> None:
    if not keys:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chow_seen_records (ccn, buyer_name, effective_date) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            keys,
        )
    conn.commit()


def fetch_chow_deals(conn) -> list[dict]:
    """
    Download latest CHOW CSV and return ownership-change rows never seen
    before, as deal dicts ready for the extraction pipeline.

    "New" used to mean "effective date after a rolling 90-day cutoff", but
    CHOW's EFFECTIVE DATE lags real filing/publication time significantly
    (confirmed 2026-09-22: a July-2026-published file's newest effective
    date was still only 2026-02-01), so a 90-day window could never match
    anything once "today" drifted far enough past that -- CHOW-sourced
    deal discovery had been silently producing zero results for a long
    time as a result. "New" is now tracked via the chow_seen_records
    table instead: every (ccn, buyer_name, effective_date) key this CSV
    has ever contained gets recorded here regardless of its effective
    date, and only genuinely new keys are considered at all on subsequent
    runs. Of those, only ones with a reasonably recent effective date
    (CHOW_RECENCY_DAYS) actually become deal dicts -- the full historical
    file goes back to 2016, and most of that is too old to be a "live
    feed" signal even the first time it's seen; this just keeps the
    dataset's older tail from ever being revisited, same as any other row.
    """
    rows = None
    discovered = _discover_chow_csv_url()
    urls = ([discovered] if discovered else []) + CHOW_URLS
    for url in urls:
        try:
            rows = _download_chow_csv(url)
            logger.info(f"Downloaded {len(rows)} CHOW records from {url}")
            break
        except Exception as e:
            logger.warning(f"Failed to download {url}: {e}")
            continue

    if not rows:
        logger.error("Could not download any CHOW CSV file")
        health.source_failed("CHOW", "could not download any CHOW CSV file")
        return []

    already_seen = _load_seen_chow_keys(conn)
    home_state = _load_home_states(conn)
    newly_seen_keys = []
    deals = []
    seen_this_run = set()

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

        buyer   = row.get("ORGANIZATION NAME - BUYER", "").strip()
        seller  = row.get("ORGANIZATION NAME - SELLER", "").strip()
        ccn     = row.get("CCN - BUYER", "").strip()
        state   = row.get("ENROLLMENT STATE - BUYER", "").strip()
        chow_type = row.get("CHOW TYPE TEXT", "CHANGE OF OWNERSHIP").strip()

        if not buyer or not ccn:
            continue

        key = (ccn, buyer, effective_date_str)
        if key in already_seen or key in seen_this_run:
            continue
        seen_this_run.add(key)
        newly_seen_keys.append(key)

        # Mark-as-seen applies to every never-before-seen row regardless of
        # age (above); only recent ones actually become deal candidates.
        if effective_date < date.today() - timedelta(days=CHOW_RECENCY_DAYS):
            continue

        # The CSV suffixes a home's CCN on a repeat CHOW (475025B, 335331001),
        # and its state is the buyer's enrollment state, which can differ
        # from the home's (Stonehenge of Orem, UT, listed under CA): the
        # deal gets the home's own CCN and state (audit 2026-10-21)
        ccn = home_ccn(ccn)
        state = home_state.get(ccn) or state

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

    _mark_chow_keys_seen(conn, newly_seen_keys)
    logger.info(f"Found {len(deals)} new CHOW records ({len(already_seen)} previously seen)")
    return deals


def home_ccn(ccn: str) -> str:
    """The six-character CMS certification number, without the CHOW file's
    repeat-sale suffix."""
    ccn = (ccn or "").strip().upper()
    return ccn[:6] if len(ccn) > 6 else ccn


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


def get_chow_operator_names(state: str) -> list[str]:
    """Return all unique buyer names from CHOW for a given state, with no date filter.
    Used to seed UCC debtor searches for states where the CHOW data predates the
    pipeline's cutoff window (so the names aren't in the DB yet).
    """
    rows = None
    discovered = _discover_chow_csv_url()
    urls = ([discovered] if discovered else []) + CHOW_URLS
    for url in urls:
        try:
            rows = _download_chow_csv(url)
            break
        except Exception as e:
            logger.warning(f"Failed to download CHOW for operator names: {e}")
            continue

    if not rows:
        # UCC searches for this state then run on live deal names only
        health.source_failed(f"CHOW {state} UCC seed names", "could not download CHOW CSV")
        return []

    names = sorted(set(
        r.get("ORGANIZATION NAME - BUYER", "").strip()
        for r in rows
        if r.get("ENROLLMENT STATE - BUYER", "").strip() == state
        and r.get("ORGANIZATION NAME - BUYER", "").strip()
    ))
    logger.info(f"CHOW: {len(names)} unique {state} buyer names for UCC search seeding")
    return names


def get_chow_source_id(conn) -> str:
    """Ensure the CHOW source exists in the sources table and return its ID."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sources (name, url, source_type, last_fetched_at)
            VALUES (%s, %s, 'chow', NOW())
            ON CONFLICT (url) DO UPDATE SET last_fetched_at = NOW()
            RETURNING id
        """, (CHOW_SOURCE_NAME, CHOW_SOURCE_URL))
        return cur.fetchone()[0]
