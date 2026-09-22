"""
scripts/enrich_ucc_facility_names.py

Enriches facility_names on UCC deals where facility_names is just a copy of the
debtor name (the default set by the UCC integrator when no collateral description
is available).

Two-phase approach:
  Phase 1 — CHOW DBA lookup (free, high confidence):
    Downloads CHOW CSV, normalizes names, matches buyer → DBA facility name.
    Handles KY opco entities like "DJLM OPERATIONS LLC" → "LETCHER MANOR".

  Phase 2 — LLC-suffix strip for already-descriptive names (free, medium confidence):
    Names containing nursing/rehab/health/care/manor/villa etc. that don't already
    look like opco wrappers → strip corporate suffix for cleaner display.

  Skipped:
    - Person names (NY debtors): they ARE the operator; no facility name to derive
    - Opco-style names NOT in CHOW: Claude's guesses are medium-confidence at best
      (e.g. "GLASGOW KY OPCO LLC" → Claude guesses "Glasgow Nursing Home" but
      actual name may differ). Better to leave blank than store wrong data.

Run:
  cd nursing-home-live-feed
  source .env && venv/bin/python3 scripts/enrich_ucc_facility_names.py

Flags:
  --dry-run    print what would change without writing to DB
  --state KY   limit to one state
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import sys
import warnings

import psycopg2
import psycopg2.extras
import requests

sys.path.insert(0, ".")
from config import get_config
from scraper.chow import _discover_chow_csv_url

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Fallback only -- had its own hardcoded (and stale, as of 2026-09-22) CHOW
# URL duplicating scraper/chow.py's; now prefers that module's self-discovery
# API and only falls back to this if the discovery lookup itself fails.
CHOW_URL_FALLBACK = (
    "https://data.cms.gov/sites/default/files/2026-07/"
    "cf019cb8-b8ce-45fc-a912-d1ee9a83ca1c/SNF_CHOW_2026.07.17.csv"
)

FACILITY_KEYWORDS = re.compile(
    r"nursing|rehabilitation|rehab|health\s*cen|care\s*cen|manor|villa|haven|"
    r"springs|trails|terrace|garden|meadow|creek|oaks|pines|hills|ridge|"
    r"lodge|view|court|place|pointe|landings|commons",
    re.I,
)
OPCO_KEYWORDS = re.compile(
    r"\bopco\b|\boperations?\b|\bholdings?\b|\bgroup\b|\boperating\b", re.I
)
PERSON_NAME = re.compile(r"^[A-Z][a-z]+ [A-Z][a-z]+$|^[A-Z]+ [A-Z]+$")
CORP_SUFFIX = re.compile(
    r",?\s*(LLC|L\.L\.C\.|INC\.?|CORP\.?|LTD\.?|L\.P\.|LP|CO\.)$", re.I
)


def _normalize(s: str) -> str:
    s = s.upper().strip()
    s = re.sub(r"[.,]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _build_chow_map() -> dict[str, str]:
    """Download CHOW CSV and build normalized-buyer → DBA map."""
    url = _discover_chow_csv_url() or CHOW_URL_FALLBACK
    logger.info(f"Downloading CHOW CSV from {url}...")
    resp = requests.get(url, timeout=60, verify=False)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    chow = {}
    for r in reader:
        buyer = r.get("ORGANIZATION NAME - BUYER", "").strip()
        dba   = r.get("DOING BUSINESS AS NAME - BUYER", "").strip()
        if buyer and dba:
            chow[_normalize(buyer)] = dba
    logger.info(f"CHOW map: {len(chow)} buyer→DBA entries")
    return chow


def _strip_suffix(name: str) -> str:
    """Strip corporate suffix and trailing comma/space."""
    return CORP_SUFFIX.sub("", name).strip()


def _classify_debtor(debtor: str) -> str:
    """
    Returns one of: 'person', 'opco', 'facility', 'other'
    """
    d = debtor.strip()
    # Three-word patterns with all caps or proper case → person name
    words = d.replace(",", "").split()
    if 2 <= len(words) <= 3 and all(w.replace(".", "").isalpha() for w in words):
        # No LLC/Inc/etc at end — likely a person
        if not re.search(r"\bllc\b|\binc\b|\bcorp\b|\bltd\b|\blp\b", d, re.I):
            return "person"
    if OPCO_KEYWORDS.search(d) and not FACILITY_KEYWORDS.search(d):
        return "opco"
    if FACILITY_KEYWORDS.search(d):
        return "facility"
    return "other"


def _is_just_debtor_copy(facility_names: list[str], debtor: str) -> bool:
    """True when facility_names is just [debtor] with possible case/whitespace variation."""
    if len(facility_names) != 1:
        return False
    return facility_names[0].strip().lower() == debtor.strip().lower()


def run(dry_run: bool = False, state_filter: str | None = None):
    config = get_config()
    chow_map = _build_chow_map()

    conn = psycopg2.connect(config.database_url)
    psycopg2.extras.register_uuid()

    try:
        with conn.cursor() as cur:
            query = """
                SELECT id, operator_names[1] AS debtor, facility_names, states[1] AS state
                FROM deals
                WHERE extraction_model = 'ucc_filing'
                  AND array_length(operator_names, 1) > 0
            """
            params = []
            if state_filter:
                query += " AND states[1] = %s"
                params.append(state_filter)
            cur.execute(query, params)
            rows = cur.fetchall()
        logger.info(f"Loaded {len(rows)} UCC deals to evaluate")

        updated_chow = 0
        updated_strip = 0
        skipped_person = 0
        skipped_no_match = 0
        skipped_already_ok = 0

        for deal_id, debtor, facility_names, state in rows:
            if not debtor:
                continue

            facility_names = facility_names or []
            # Skip if facility_names already differs from debtor (already enriched)
            if not _is_just_debtor_copy(facility_names, debtor):
                skipped_already_ok += 1
                continue

            category = _classify_debtor(debtor)

            if category == "person":
                skipped_person += 1
                continue

            # Phase 1: CHOW DBA lookup
            chow_key = _normalize(debtor)
            if chow_key in chow_map:
                new_name = chow_map[chow_key]
                if new_name.strip().lower() != debtor.strip().lower():
                    logger.info(f"  CHOW [{state}] {debtor[:45]:<45} → {new_name}")
                    if not dry_run:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE deals SET facility_names = %s WHERE id = %s",
                                ([new_name], deal_id)
                            )
                    updated_chow += 1
                    continue

            # Phase 2: strip LLC suffix for facility-like names
            if category == "facility":
                stripped = _strip_suffix(debtor)
                if stripped and stripped.lower() != debtor.lower():
                    logger.info(f"  STRIP [{state}] {debtor[:45]:<45} → {stripped}")
                    if not dry_run:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE deals SET facility_names = %s WHERE id = %s",
                                ([stripped], deal_id)
                            )
                    updated_strip += 1
                    continue

            skipped_no_match += 1

        if not dry_run:
            conn.commit()

        print("\n=== Enrichment summary ===")
        print(f"  CHOW DBA updates:      {updated_chow}")
        print(f"  LLC-strip updates:     {updated_strip}")
        print(f"  Person names skipped:  {skipped_person}")
        print(f"  No match (opco/other): {skipped_no_match}")
        print(f"  Already enriched:      {skipped_already_ok}")
        if dry_run:
            print("  [DRY RUN — no changes written]")

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state", default=None, help="Limit to one state code (e.g. KY)")
    args = parser.parse_args()
    run(dry_run=args.dry_run, state_filter=args.state)
