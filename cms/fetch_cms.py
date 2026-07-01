"""
CMS dataset fetcher.
Downloads the latest Ownership and Care Compare data from data.cms.gov
and upserts into the local Postgres database.

Run on a schedule (weekly) to keep CMS data fresh.
"""

import csv
import io
import logging
import re
import time
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import get_config

logger = logging.getLogger(__name__)
config = get_config()

# The raw CMS "All Owners" enrollment-system API (data-api UUID
# 128fb95f-...) is keyed by PECOS Enrollment ID, NOT a CMS Certification
# Number — it can't be joined to cms_facilities.ccn at all. We instead use
# the Provider Data Catalog "Ownership" export (NH_Ownership_*.csv), same
# family as the Provider Info file Care Compare uses, which carries a real
# CCN, the correct facility state, and human-readable Owner Type.
OWNERSHIP_METASTORE_URL = (
    "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/y2hd-n93e"
)

# Fallback if the metastore lookup fails — last known-good URL.
OWNERSHIP_CSV_FALLBACK_URL = (
    "https://data.cms.gov/provider-data/sites/default/files/resources/"
    "211fec82817d05edbce24f590f73fa9e_1781194537/NH_Ownership_Jun2026.csv"
)


def _discover_ownership_csv_url() -> str | None:
    try:
        resp = requests.get(OWNERSHIP_METASTORE_URL, timeout=30)
        resp.raise_for_status()
        dist = resp.json().get("distribution", [])
        if dist and dist[0].get("downloadURL"):
            return dist[0]["downloadURL"]
    except Exception as e:
        logger.warning(f"Metastore lookup failed for Ownership dataset: {e}")
    return None


def _parse_association_date(val: str):
    """'since 01/25/2012' -> '2012-01-25'"""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", val or "")
    if not m:
        return None
    mm, dd, yyyy = m.groups()
    return f"{yyyy}-{mm}-{dd}"


def fetch_and_load_all():
    """Main entry point — fetch both datasets and load into Postgres."""
    conn = psycopg2.connect(config.database_url)
    try:
        logger.info("Loading CMS Ownership dataset...")
        load_ownership(conn)

        logger.info("Loading CMS Care Compare dataset...")
        from matcher.carecompare import load_care_compare
        load_care_compare(conn)

        conn.commit()
        logger.info("CMS data load complete.")
    except Exception as e:
        conn.rollback()
        logger.error(f"CMS load failed: {e}")
        raise
    finally:
        conn.close()


def load_ownership(conn):
    """Download the latest Ownership CSV and upsert into cms_ownership_records."""
    url = _discover_ownership_csv_url() or OWNERSHIP_CSV_FALLBACK_URL
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    logger.info(f"Downloaded {len(rows)} Ownership records from {url}")

    now = datetime.now(timezone.utc)
    total = _upsert_ownership(conn, rows, now)
    conn.commit()
    logger.info(f"Ownership load done: {total} total records")


def load_care_compare(conn):
    """Fetch all Care Compare provider records and upsert into cms_facilities."""
    now = datetime.now(timezone.utc)
    offset = _get_checkpoint(conn, "carecompare")
    total = 0

    logger.info(f"Care Compare load starting at offset {offset}")

    while True:
        url = (
            f"{config.cms_api_base}/{config.cms_carecompare_dataset}.json"
            f"?$select={CMS_CARECOMPARE_FIELDS}"
            f"&$limit={config.cms_page_size}&$offset={offset}"
        )
        rows = _fetch_page(url)
        if not rows:
            break

        _upsert_care_compare(conn, rows, now)
        total += len(rows)
        offset += len(rows)
        _save_checkpoint(conn, "carecompare", offset)
        conn.commit()
        logger.info(f"Care Compare: loaded {total} records (offset {offset})")

        if len(rows) < config.cms_page_size:
            break
        time.sleep(0.5)

    _clear_checkpoint(conn, "carecompare")
    conn.commit()
    logger.info(f"Care Compare load done: {total} total records")


# ── Checkpoint helpers ────────────────────────────────────────
# Store progress in a simple key/value table so crashed loads resume
# instead of restarting from page 0.

def _ensure_checkpoint_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cms_load_checkpoints (
                key   TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    conn.commit()


def _get_checkpoint(conn, key: str) -> int:
    _ensure_checkpoint_table(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM cms_load_checkpoints WHERE key = %s", (key,))
        row = cur.fetchone()
        return row[0] if row else 0


def _save_checkpoint(conn, key: str, offset: int):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO cms_load_checkpoints (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (key, offset))


def _clear_checkpoint(conn, key: str):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM cms_load_checkpoints WHERE key = %s", (key,))


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    reraise=True,
)
def _fetch_page(url: str, limit: int = None, offset: int = None) -> list[dict]:
    params = {}
    if limit is not None:
        params["size"] = limit
    if offset is not None:
        params["offset"] = offset
    try:
        resp = requests.get(url, params=params or None, timeout=config.request_timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        if e.response.status_code == 429:
            logger.warning("CMS API rate limited, backing off...")
            raise requests.ConnectionError("rate limited")
        logger.error(f"HTTP error fetching CMS page: {url} — {e}")
        return []
    except (requests.Timeout, requests.ConnectionError):
        raise
    except Exception as e:
        logger.error(f"Failed to fetch CMS page: {url} — {e}")
        return []


def _upsert_ownership(conn, rows: list[dict], now) -> int:
    """Map NH_Ownership CSV columns to our schema and upsert."""
    records = []
    for r in rows:
        ccn = r.get("CMS Certification Number (CCN)", "").strip()
        owner_name = r.get("Owner Name", "").strip()
        if not ccn or not owner_name:
            continue
        start_date = _parse_association_date(r.get("Association Date", ""))
        if not start_date:
            continue
        pct_raw = r.get("Ownership Percentage", "").strip().rstrip("%")
        records.append((
            ccn,
            r.get("Provider Name", "").strip(),
            owner_name,
            r.get("Owner Type", "").strip(),
            r.get("Role played by Owner or Manager in Facility", "").strip(),
            _safe_float(pct_raw),
            r.get("State", "").strip(),
            start_date,
            now,
        ))

    if not records:
        return 0

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO cms_ownership_records
                (ccn, provider_name, owner_name, owner_type, owner_role,
                 ownership_percentage, provider_state, ownership_start_date, cms_refreshed_at)
            VALUES %s
            ON CONFLICT (ccn, owner_name, ownership_start_date, owner_role)
            DO UPDATE SET
                provider_name        = EXCLUDED.provider_name,
                owner_type           = EXCLUDED.owner_type,
                ownership_percentage = EXCLUDED.ownership_percentage,
                provider_state       = EXCLUDED.provider_state,
                cms_refreshed_at     = EXCLUDED.cms_refreshed_at
        """, records)
    return len(records)


def _upsert_care_compare(conn, rows: list[dict], now):
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
        """, [
            (
                r.get("federal_provider_number"),
                r.get("provider_name"),
                r.get("provider_state"),
                r.get("provider_city"),
                r.get("provider_zip_code"),
                r.get("ownership_type"),
                r.get("provider_type"),
                _safe_int(r.get("number_of_certified_beds")),
                _safe_int(r.get("overall_rating")),
                _safe_int(r.get("staffing_rating")),
                _safe_int(r.get("health_inspection_rating")),
                r.get("in_sff", "").lower() == "y",
                r.get("in_sff_candidate", "").lower() == "y",
                now,
            )
            for r in rows
            if r.get("federal_provider_number")
        ])


def _safe_float(val):
    try: return float(val)
    except: return None

def _safe_int(val):
    try: return int(val)
    except: return None

def _safe_date(val):
    if not val: return None
    try:
        return val.split("T")[0]
    except: return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetch_and_load_all()
