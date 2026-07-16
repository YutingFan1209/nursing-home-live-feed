"""
CMS Care Compare enrichment.
Fetches Provider Info CSV (NH_ProviderInfo_MonYYYY.csv) which contains
5-star ratings, staffing ratings, special focus status, and bed counts.

The CSV URL embeds a content hash that rotates every release, so it can't
be hardcoded reliably. URL discovery: the "Provider Information" metastore
entry (data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/4pq5-n9py)
always resolves to the current distribution's downloadURL — see
_discover_provider_info_url(). PROVIDER_INFO_URLS is only a last-resort
fallback if that lookup fails.

Column reference (verified against the June 2026 release):
  CMS Certification Number (CCN) -> ccn
  Provider Name                  -> provider_name
  State                          -> provider_state
  City/Town                      -> provider_city
  ZIP Code                       -> provider_zip
  Overall Rating                 -> five_star_rating
  Staffing Rating                -> staffing_rating
  Health Inspection Rating       -> health_insp_rating
  Special Focus Status           -> sff_flag ("SFF") / sff_candidate_flag ("SFF Candidate")
  Number of Certified Beds       -> bed_count
  Ownership Type                 -> ownership_type
"""

import csv
import io
import logging
import requests
import psycopg2.extras
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# "Provider Information" dataset metastore entry — resolves to the current
# month's CSV downloadURL. The direct CSV URLs embed a content hash that
# rotates every release, so they can't be hardcoded reliably; this metastore
# endpoint is the stable, discoverable pointer to whatever is current.
PROVIDER_INFO_METASTORE_URL = (
    "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/4pq5-n9py"
)

# Fallback if the metastore lookup fails — last known-good URLs, most recent first.
PROVIDER_INFO_URLS = [
    "https://data.cms.gov/provider-data/sites/default/files/resources/bc7015f6a981fa7e209809e021f8f0cc_1781194538/NH_ProviderInfo_Jun2026.csv",
]


def _discover_provider_info_url() -> str | None:
    try:
        resp = requests.get(PROVIDER_INFO_METASTORE_URL, timeout=30)
        resp.raise_for_status()
        dist = resp.json().get("distribution", [])
        if dist and dist[0].get("downloadURL"):
            return dist[0]["downloadURL"]
    except Exception as e:
        logger.warning(f"Metastore lookup failed for Provider Information dataset: {e}")
    return None


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
    Tries the metastore-discovered current URL first, then falls back to
    the last known-good URLs.
    """
    rows = None
    discovered = _discover_provider_info_url()
    urls = ([discovered] if discovered else []) + PROVIDER_INFO_URLS
    for url in urls:
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
        ccn = r.get("CMS Certification Number (CCN)", "").strip()
        if not ccn:
            continue
        special_focus = r.get("Special Focus Status", "").strip()
        records.append((
            ccn,
            r.get("Provider Name", "").strip(),
            r.get("State", "").strip(),
            r.get("City/Town", "").strip(),
            r.get("ZIP Code", "").strip(),
            r.get("Ownership Type", "").strip(),
            r.get("Provider Type", "").strip(),
            _safe_int(r.get("Number of Certified Beds")),
            _safe_int(r.get("Overall Rating")),
            _safe_int(r.get("Staffing Rating")),
            _safe_int(r.get("Health Inspection Rating")),
            special_focus == "SFF",
            special_focus == "SFF Candidate",
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
