"""
CMS Care Compare enrichment.
Fetches Provider Info CSV (NH_ProviderInfo_MonYYYY.csv) which contains
5-star ratings, staffing ratings, SFF flags, and bed counts.

CMS has deprecated their SODA and provider-data APIs (both return 403).
Direct CSV downloads still work. The URL changes monthly following
the pattern: data.cms.gov/provider-data/sites/default/files/.../NH_ProviderInfo_MonYYYY.csv

URL discovery: We maintain a list of recent known URLs and try each in order.
When a new month's file is released, add its URL to PROVIDER_INFO_URLS.

Column reference (from NH Data Dictionary, Feb 2026):
  Federal Provider Number  -> ccn
  Provider Name            -> provider_name
  Provider State           -> provider_state
  Overall Rating           -> five_star_rating
  Staffing Rating          -> staffing_rating
  Health Inspection Rating -> health_insp_rating
  In SFF                   -> sff_flag (Y/N)
  In SFF Candidate         -> sff_candidate_flag (Y/N)
  Number of Certified Beds -> bed_count
  Ownership Type           -> ownership_type
"""

import csv
import io
import logging
import requests
import psycopg2.extras
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Known Provider Info CSV URLs — most recent first
# Add new URL here each month when CMS releases updated data
# Check: https://data.cms.gov/provider-data/topics/nursing-homes
PROVIDER_INFO_URLS = [
    "https://data.cms.gov/provider-data/sites/default/files/resources/d47f87c5e9c8a51e51e9e83e2e0b7fca_1746230908/NH_ProviderInfo_Apr2025.csv",
    "https://data.cms.gov/provider-data/sites/default/files/resources/77b5ef7cfe32b13c9e1cef6e47ee3be1_1741651212/NH_ProviderInfo_Jan2025.csv",
    "https://data.cms.gov/provider-data/sites/default/files/resources/8d6f74d4c890d13ed6e31a7e71f28c2d_1736888760/NH_ProviderInfo_Oct2024.csv",
]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
def _download_provider_csv(url: str) -> list[dict]:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return list(reader)


def load_care_compare(conn):
    """
    Download the latest Provider Info CSV and upsert into cms_facilities.
    Tries each known URL until one works.
    """
    rows = None
    for url in PROVIDER_INFO_URLS:
        try:
            rows = _download_provider_csv(url)
            logger.info(f"Downloaded {len(rows)} Care Compare records from {url}")
            break
        except Exception as e:
            logger.warning(f"Failed to download {url}: {e}")
            continue

    if not rows:
        logger.error(
            "Could not download Care Compare CSV. "
            "Add the latest URL to PROVIDER_INFO_URLS in carecompare.py — "
            "check https://data.cms.gov/provider-data/topics/nursing-homes"
        )
        return 0

    now = datetime.now(timezone.utc)
    records = []
    for r in rows:
        ccn = r.get("Federal Provider Number", "").strip()
        if not ccn:
            continue
        records.append((
            ccn,
            r.get("Provider Name", "").strip(),
            r.get("Provider State", "").strip(),
            r.get("Provider City", "").strip(),
            r.get("Provider Zip Code", "").strip(),
            r.get("Ownership Type", "").strip(),
            r.get("Provider Type", "").strip(),
            _safe_int(r.get("Number of Certified Beds")),
            _safe_int(r.get("Overall Rating")),
            _safe_int(r.get("Staffing Rating")),
            _safe_int(r.get("Health Inspection Rating")),
            r.get("In SFF", "").strip().upper() == "Y",
            r.get("In SFF Candidate", "").strip().upper() == "Y",
            now,
        ))

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO cms_facilities
                (ccn, provider_name, provider_state, provider_city, provider_zip,
                 ownership_type, provider_type, bed_count,
                 five_star_rating, staffing_rating, health_insp_rating,
                 sff_flag, sff_candidate_flag, cms_refreshed_at)
            VALUES %s
            ON CONFLICT (ccn) DO UPDATE SET
                provider_name      = EXCLUDED.provider_name,
                five_star_rating   = EXCLUDED.five_star_rating,
                staffing_rating    = EXCLUDED.staffing_rating,
                health_insp_rating = EXCLUDED.health_insp_rating,
                sff_flag           = EXCLUDED.sff_flag,
                sff_candidate_flag = EXCLUDED.sff_candidate_flag,
                bed_count          = EXCLUDED.bed_count,
                cms_refreshed_at   = EXCLUDED.cms_refreshed_at
        """, records)

    logger.info(f"Care Compare: upserted {len(records)} facilities")
    return len(records)


def enrich_matches(matches: list[dict], states: list[str], conn) -> list[dict]:
    """Attach quality data from cms_facilities to ownership matches."""
    if not matches:
        return matches
    ccns = [m["ccn"] for m in matches if m.get("ccn")]
    if not ccns:
        return matches
    cc_by_ccn = {r["ccn"]: r for r in _fetch_care_compare(ccns, states, conn)}
    enriched = []
    for match in matches:
        cc = cc_by_ccn.get(match.get("ccn"), {})
        enriched.append({
            **match,
            "five_star_rating":   cc.get("five_star_rating"),
            "staffing_rating":    cc.get("staffing_rating"),
            "health_insp_rating": cc.get("health_insp_rating"),
            "sff_flag":           cc.get("sff_flag", False),
            "sff_candidate_flag": cc.get("sff_candidate_flag", False),
            "bed_count":          cc.get("bed_count"),
            "ownership_type":     cc.get("ownership_type"),
        })
    return enriched


def _fetch_care_compare(ccns: list[str], states: list[str], conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT ccn, provider_name, provider_state, five_star_rating,
                   staffing_rating, health_insp_rating, sff_flag,
                   sff_candidate_flag, bed_count, ownership_type
            FROM cms_facilities WHERE ccn = ANY(%s)
        """, (ccns,))
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def flag_policy_risks(matches: list[dict]) -> list[dict]:
    """Flag regulatory risk indicators on each match."""
    for match in matches:
        flags = []
        if match.get("sff_flag"):
            flags.append("Special Focus Facility")
        if match.get("sff_candidate_flag"):
            flags.append("SFF Candidate")
        if match.get("five_star_rating") and match["five_star_rating"] <= 2:
            flags.append(f"Low quality rating ({match['five_star_rating']}★)")
        if match.get("staffing_rating") and match["staffing_rating"] <= 2:
            flags.append("Low staffing rating")
        match["policy_flags"] = flags
    return matches


def _safe_int(val):
    try:
        return int(val) if val and str(val).strip() else None
    except (ValueError, TypeError):
        return None
