"""
scripts/enrich_ucc_facility_names.py

Enriches facility_names on UCC deals where facility_names is just a copy of the
debtor name (the default set by the UCC integrator when no collateral description
is available).

Phases, highest confidence first:
  Phase 0 — CMS exact match (added 2026-09-23, no download, runs from local tables):
    Debtor name, normalized (case, punctuation, &/AND, LLC/Inc/etc. removed),
    identical to exactly one cms_facilities.provider_name in the same state
    -> that facility. Failing that, identical to an organizational owner in
    cms_ownership_records that owns exactly one CCN in the state -> that CCN.
    Sets facility_names to the CMS provider name AND records the CCN in
    cms_matches (match_method 'ucc_debtor_exact' / 'ucc_debtor_owner_exact',
    score 100). Deal stage is deliberately left alone: matching the borrower
    to its facility says nothing about whether ownership changed.

  --loose-report PATH (review only, never written to the DB): after dropping
    role words (OPERATOR, REALTY, HOLDINGS, ...) from both sides, lists the
    deals whose debtor points at exactly one facility in-state by prefix/
    equality. Mostly right, but not certain enough to apply unreviewed.

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


def _build_chow_map() -> dict[tuple[str, str], set[tuple[str, str]]]:
    """Download CHOW CSV and build (normalized buyer, state) -> {(CCN, DBA)}.
    Keyed by state and kept as a set: 95 buyers (e.g. AHF OHIO INC) bought
    several facilities, and the old buyer -> DBA dict silently kept
    whichever row came last."""
    url = _discover_chow_csv_url() or CHOW_URL_FALLBACK
    logger.info(f"Downloading CHOW CSV from {url}...")
    resp = requests.get(url, timeout=60, verify=False)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    chow = {}
    for r in reader:
        buyer = r.get("ORGANIZATION NAME - BUYER", "").strip()
        dba   = r.get("DOING BUSINESS AS NAME - BUYER", "").strip()
        state = r.get("ENROLLMENT STATE - BUYER", "").strip()
        ccn   = r.get("CCN - BUYER", "").strip()
        if buyer and dba:
            chow.setdefault((_normalize(buyer), state), set()).add((ccn, dba))
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


SUFFIX_WORDS = re.compile(
    r"\b(LLC|L L C|INC|INCORPORATED|LP|L P|LLP|CORP|CORPORATION|CO|COMPANY|LTD|PLLC|PC|THE)\b")
ROLE_WORDS = re.compile(
    r"\b(OPERATOR|OPERATORS|OPERATIONS|OPERATING|OPCO|PROPCO|REALTY|REAL ESTATE|PROPERTY|"
    r"PROPERTIES|HOLDINGS|HOLDING|GROUP|MANAGEMENT|ASSOCIATES|ENTERPRISES|LEASING|PARTNERS|"
    r"ACQUISITION|HEALTHCARE|HEALTH CARE|CARE|NURSING|REHABILITATION|REHAB|AND|CENTER|CTR|"
    r"HOME|OF|AT|FOR)\b")


def _entity_key(name: str) -> str:
    """Normalization for exact matching against CMS names: stricter than
    _normalize (which only has to line up with CHOW's own formatting)."""
    s = (name or "").upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = SUFFIX_WORDS.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _core_key(name: str) -> str:
    return re.sub(r"\s+", " ", ROLE_WORDS.sub(" ", _entity_key(name))).strip()


def _build_cms_maps(conn):
    """(entity_key, state) -> {(ccn, provider_name)} for facility names and
    for organizational owners, plus per-state facility lists for the loose
    report."""
    facilities, owners, by_state, names_by_ccn = {}, {}, {}, {}
    with conn.cursor() as cur:
        cur.execute("SELECT ccn, provider_name, provider_state FROM cms_facilities")
        for ccn, name, state in cur.fetchall():
            facilities.setdefault((_entity_key(name), state), set()).add((ccn, name))
            names_by_ccn[ccn] = name
            by_state.setdefault(state, []).append((ccn, name, _entity_key(name), _core_key(name)))
        cur.execute("""
            SELECT DISTINCT ccn, provider_name, owner_name, provider_state
            FROM cms_ownership_records WHERE owner_type ILIKE 'org%%'
        """)
        for ccn, provider, owner, state in cur.fetchall():
            owners.setdefault((_entity_key(owner), state), {})[ccn] = (provider, owner)
    return facilities, owners, by_state, names_by_ccn


def _cms_exact_match(debtor: str, state: str, facilities, owners):
    """Returns (ccn, provider_name, owner_name, match_method) or None."""
    key = (_entity_key(debtor), state)
    hits = facilities.get(key, set())
    if len(hits) == 1:
        ccn, provider = next(iter(hits))
        return ccn, provider, debtor, "ucc_debtor_exact"
    owned = owners.get(key, {})
    if not hits and len(owned) == 1:
        ccn, (provider, owner) = next(iter(owned.items()))
        return ccn, provider, owner, "ucc_debtor_owner_exact"
    return None


def _loose_match(debtor: str, state: str, by_state):
    core = _core_key(debtor)
    if len(core) < 4:
        return []
    return sorted({
        (ccn, name) for ccn, name, key, fac_core in by_state.get(state, [])
        if fac_core == core or (len(core) >= 6 and (fac_core.startswith(core + " ") or key.startswith(core + " ")))
    })


def _record_cms_match(conn, deal_id, state, ccn, provider, owner, method):
    with conn.cursor() as cur:
        cur.execute("UPDATE deals SET facility_names = %s WHERE id = %s", ([provider], deal_id))
        # always our own row, even if a fuzzy match already found this CCN:
        # that CCN has one row per CMS owner record, which must keep its own
        # match_method/score
        cur.execute("""
            SELECT 1 FROM cms_matches WHERE deal_id = %s AND ccn = %s AND match_method = %s
        """, (deal_id, ccn, method))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO cms_matches (deal_id, ccn, provider_name, owner_name, provider_state,
                                         match_score, match_method, matched_on_field)
                VALUES (%s, %s, %s, %s, %s, 100, %s, 'operator_names')
            """, (deal_id, ccn, provider, owner, state, method))


def _is_just_debtor_copy(facility_names: list[str], debtor: str) -> bool:
    """True when facility_names is just [debtor] with possible case/whitespace variation."""
    if len(facility_names) != 1:
        return False
    return facility_names[0].strip().lower() == debtor.strip().lower()


def run(dry_run: bool = False, state_filter: str | None = None,
        loose_report: str | None = None, conn=None, use_chow: bool = True,
        strip: bool = False):
    """conn: pass an open connection (main.py does) to run inside its
    session; it is committed but not closed. use_chow=False skips the CHOW
    CSV download (Phase 1), leaving the local-table phases only."""
    chow_map = {}
    if use_chow:
        try:
            chow_map = _build_chow_map()
        except Exception as e:
            logger.warning(f"CHOW map unavailable ({e}) -- skipping Phase 1")

    owns_conn = conn is None
    if owns_conn:
        conn = psycopg2.connect(get_config().database_url)
    psycopg2.extras.register_uuid()
    facilities, owners, by_state, names_by_ccn = _build_cms_maps(conn)
    loose_rows = []

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

        updated_cms = 0
        updated_chow = 0
        skipped_chow_ambiguous = 0
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

            # Phase 0: CMS exact match
            match = _cms_exact_match(debtor, state, facilities, owners)
            if match:
                ccn, provider, owner, method = match
                logger.info(f"  CMS  [{state}] {debtor[:45]:<45} → {provider} ({ccn}, {method})")
                if not dry_run:
                    _record_cms_match(conn, deal_id, state, ccn, provider, owner, method)
                updated_cms += 1
                continue

            if loose_report:
                hits = _loose_match(debtor, state, by_state)
                if len(hits) == 1:
                    loose_rows.append((deal_id, state, debtor, hits[0][0], hits[0][1]))

            category = _classify_debtor(debtor)

            if category == "person":
                skipped_person += 1
                continue

            # Phase 1: CHOW DBA lookup
            chow_hits = chow_map.get((_normalize(debtor), state), set())
            if len(chow_hits) > 1:
                skipped_chow_ambiguous += 1
            elif chow_hits:
                ccn, new_name = next(iter(chow_hits))
                # prefer CMS's current provider name: CHOW's DBA field is
                # free text and sometimes wrong (one NJ row's DBA is its
                # staffing agency, "CLIPBOARD HEALTH")
                new_name = names_by_ccn.get(ccn, new_name)
                if new_name.strip().lower() != debtor.strip().lower():
                    logger.info(f"  CHOW [{state}] {debtor[:45]:<45} → {new_name} ({ccn or 'no CCN'})")
                    if not dry_run:
                        if ccn:
                            _record_cms_match(conn, deal_id, state, ccn, new_name, debtor, "ucc_debtor_chow_buyer")
                        else:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "UPDATE deals SET facility_names = %s WHERE id = %s",
                                    ([new_name], deal_id)
                                )
                    updated_chow += 1
                    continue

            # Phase 2 (opt-in, --strip): strip LLC suffix for facility-like
            # names. Off by default since 2026-09-23: the frontend now shows
            # the debtor itself in UCC headlines, so this only made a raw
            # debtor look like a verified facility name.
            if strip and category == "facility":
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

        if loose_report:
            with open(loose_report, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["deal_id", "state", "debtor", "candidate_ccn", "candidate_facility", "approve_y_n"])
                w.writerows([*row, ""] for row in loose_rows)
            print(f"  Loose-match candidates written to {loose_report}: {len(loose_rows)}")

        print("\n=== Enrichment summary ===")
        print(f"  CMS exact matches:     {updated_cms}")
        print(f"  CHOW DBA updates:      {updated_chow}")
        print(f"  CHOW buyer ambiguous:  {skipped_chow_ambiguous}")
        print(f"  LLC-strip updates:     {updated_strip}")
        print(f"  Person names skipped:  {skipped_person}")
        print(f"  No match (opco/other): {skipped_no_match}")
        print(f"  Already enriched:      {skipped_already_ok}")
        if dry_run:
            print("  [DRY RUN — no changes written]")

    finally:
        if owns_conn:
            conn.close()
    return updated_cms + updated_chow + updated_strip


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state", default=None, help="Limit to one state code (e.g. KY)")
    parser.add_argument("--loose-report", default=None, help="Write loose-match candidates for review to this CSV")
    parser.add_argument("--strip", action="store_true", help="Also run the LLC-suffix-strip phase (Phase 2)")
    parser.add_argument("--no-chow", action="store_true", help="Skip the CHOW CSV download (Phase 1)")
    args = parser.parse_args()
    run(dry_run=args.dry_run, state_filter=args.state, loose_report=args.loose_report, use_chow=not args.no_chow, strip=args.strip)
