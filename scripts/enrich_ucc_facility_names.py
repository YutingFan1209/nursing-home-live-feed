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
    for organizational owners, per-state facility lists for the loose
    report, and (state, city) -> facilities for the town rule."""
    facilities, owners, by_state, names_by_ccn = {}, {}, {}, {}
    with conn.cursor() as cur:
        cur.execute("SELECT ccn, provider_name, provider_state, provider_city FROM cms_facilities")
        for ccn, name, state, city in cur.fetchall():
            facilities.setdefault((_entity_key(name), state), set()).add((ccn, name))
            names_by_ccn[ccn] = name
            by_state.setdefault(state, []).append((ccn, name, _entity_key(name), _core_key(name)))
            if city:
                by_state.setdefault(("city", state, _entity_key(city)), []).append((ccn, name))
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


def _other_state_exact(debtor: str, state: str, national):
    """A UCC-1 is filed where the company is registered, not where the home
    is: "THE CENTER AT PARMER, LLC" is a Colorado LLC running The Center at
    Parmer in Austin, TX. Only a facility name that is unique nationwide.
    national: entity_key -> {(ccn, provider_name, state)}. Registered
    companies only: an unregistered name files where it does business
    ("HERITAGE PARK NURSING CENTER" in FL isn't the CA home)."""
    if not re.search(r"\b(LLC|L L C|INC|INCORPORATED|CORP|CORPORATION|LP|L P|LLP|LTD)\b",
                     re.sub(r"[^A-Z ]", " ", debtor.upper())):
        return None
    hits = {h for h in national.get(_entity_key(debtor), set()) if h[2] != state}
    return next(iter(hits)) if len(hits) == 1 else None


def _loose_match(debtor: str, state: str, by_state):
    core = _core_key(debtor)
    if len(core) < 4:
        return []
    return sorted({
        (ccn, name) for ccn, name, key, fac_core in by_state.get(state, [])
        if fac_core == core or (len(core) >= 6 and (fac_core.startswith(core + " ") or key.startswith(core + " ")))
    })


# Portfolio rules (2026-10-21, CO): a lender files against a portfolio's
# operators, property companies and parents on the same day or the next,
# so a filing's linked companions narrow down a borrower whose own name
# isn't in CMS. Colorado's Centennial portfolio: "RIO GRANDE PROPCO LLC"
# (Vium, 2025-12-03) beside "RIO GRANDE REHABILITATION AND HEALTHCARE
# CENTER LLC" (Vium, 2025-12-04).
PORTFOLIO_DAYS = 1
TOWN_ROLE_WORDS = re.compile(
    r"\b(SNF|NURSING|OPCO|PROPCO|REALTY|REAL ESTATE|PROPERTY|PROPERTIES|HOLDINGS?)\b|\b\d+\b")
# A parent links to every home its companions link to; only these words, as
# a "... VILLAGE ASSISTED LIVING" or "... COTTAGES" companion is a different
# kind of home and must stay unlinked
PARENT_WORDS = re.compile(r"\b(HOLDCO|MASTER TENANT)\b")
# HUD files against unrelated borrowers on the same day ("GRAND JUNCTION SNF"
# and "THE CENTER AT FORESIGHT", 2025-05-05), so its filings aren't a portfolio
NOT_PORTFOLIO_LENDER = re.compile(r"^(SECRETARY OF|UNITED STATES DEPARTMENT OF HOUSING)")


def _town_candidates(debtor: str, state: str, by_state):
    """Homes in the town a debtor is named after ("CRAIG SNF, LLC",
    "Monte Vista 2277 Realty LLC")."""
    town = re.sub(r"\s+", " ", TOWN_ROLE_WORDS.sub(" ", _entity_key(debtor))).strip()
    return set(by_state.get(("city", state, town), [])) if town else set()


def _portfolio_match(debtor: str, state: str, companion_ccns: set, by_state, names_by_ccn):
    """(method, [(ccn, provider)]) or None. companion_ccns: CCNs linked to
    filings by the same lender in the same state within PORTFOLIO_DAYS."""
    if not companion_ccns:
        return None
    loose = {ccn for ccn, _ in _loose_match(debtor, state, by_state)} & companion_ccns
    if len(loose) == 1:
        ccn = loose.pop()
        return "ucc_debtor_portfolio_name", [(ccn, names_by_ccn[ccn])]
    town = {ccn for ccn, _ in _town_candidates(debtor, state, by_state)} & companion_ccns
    if len(town) == 1:
        ccn = town.pop()
        return "ucc_debtor_portfolio_town", [(ccn, names_by_ccn[ccn])]
    # two or more homes: with one, an address-named property holdco ("113
    # SOUTH ROUTE 73 PROPCO HOLDCO LLC") took whichever companion happened to link
    if PARENT_WORDS.search(debtor.upper()) and len(companion_ccns) >= 2:
        return "ucc_debtor_portfolio_parent", sorted((c, names_by_ccn[c]) for c in companion_ccns)
    return None


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


# OH lists every debtor on a filing in one field, comma-separated: "670 OH
# OPCO LLC, 600 NORTH BRUSH OPCO LLC" (a VNB loan for two homes) matched no
# home as a whole, so 103 OH/CA/RI/FL filings went unlinked (2026-10-21).
# Split only after a company suffix, so "ABC, LLC" stays one name.
_DEBTOR_END = re.compile(r"\b(?:LLC|INC|L\.L\.C|LP|L\.P|CORP|CORPORATION|COMPANY|LTD)\.?(?=,\s+)", re.I)


def split_debtors(debtor: str) -> list[str]:
    parts, start = [], 0
    for m in _DEBTOR_END.finditer(debtor or ""):
        parts.append(debtor[start:m.end()])
        start = m.end() + 1  # past the comma
    parts.append((debtor or "")[start:])
    return [p.strip() for p in parts if p.strip()]


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
    national = {}
    for (key, st), found in facilities.items():
        for ccn, name in found:
            national.setdefault(key, set()).add((ccn, name, st))
    loose_rows = []

    try:
        with conn.cursor() as cur:
            query = """
                SELECT id, operator_names[1] AS debtor, facility_names, states[1] AS state,
                       lender, acquisition_date
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
            # CCNs each deal is already linked to by this script
            cur.execute("""
                SELECT deal_id, ccn FROM cms_matches
                WHERE match_method LIKE 'ucc_debtor%%' AND ccn IS NOT NULL
            """)
            linked = {}
            for deal_id, ccn in cur.fetchall():
                linked.setdefault(deal_id, set()).add(ccn)
        logger.info(f"Loaded {len(rows)} UCC deals to evaluate")
        by_lender = {}
        for deal_id, _, _, state, lender, filed in rows:
            if lender and filed and not NOT_PORTFOLIO_LENDER.match(lender.strip().upper()):
                by_lender.setdefault((state, _entity_key(lender)), []).append((filed, deal_id))
        unlinked = []

        updated_cms = 0
        updated_chow = 0
        skipped_chow_ambiguous = 0
        updated_strip = 0
        skipped_person = 0
        skipped_no_match = 0
        skipped_already_ok = 0

        for deal_id, debtor, facility_names, state, lender, filed in rows:
            if not debtor:
                continue

            facility_names = facility_names or []
            # Skip if facility_names already differs from debtor (already enriched)
            if not _is_just_debtor_copy(facility_names, debtor):
                skipped_already_ok += 1
                continue

            # Phase 0, several debtors on one filing: link each one's home
            parts = split_debtors(debtor)
            if len(parts) > 1:
                found = {}
                for part in parts:
                    m = _cms_exact_match(part, state, facilities, owners)
                    if not m:
                        hits = chow_map.get((_normalize(part), state), set())
                        ccn, name = next(iter(hits)) if len(hits) == 1 else (None, None)
                        m = (ccn, names_by_ccn.get(ccn, name), part, "ucc_debtor_chow_buyer") if ccn else None
                    if m:
                        found.setdefault(m[0], m)
                if found:
                    for ccn, provider, owner, method in found.values():
                        logger.info(f"  CMS  [{state}] {owner[:45]:<45} → {provider} ({ccn}, {method}, one of {len(parts)} debtors)")
                        if not dry_run:
                            _record_cms_match(conn, deal_id, state, ccn, provider, owner, method)
                        linked.setdefault(deal_id, set()).add(ccn)
                    if not dry_run:
                        with conn.cursor() as cur:
                            cur.execute("UPDATE deals SET facility_names = %s WHERE id = %s",
                                        (sorted({m[1] for m in found.values()}), deal_id))
                    updated_cms += 1
                    continue

            # Phase 0: CMS exact match
            match = _cms_exact_match(debtor, state, facilities, owners)
            if match:
                ccn, provider, owner, method = match
                logger.info(f"  CMS  [{state}] {debtor[:45]:<45} → {provider} ({ccn}, {method})")
                if not dry_run:
                    _record_cms_match(conn, deal_id, state, ccn, provider, owner, method)
                linked.setdefault(deal_id, set()).add(ccn)
                updated_cms += 1
                continue

            # Phase 0b: the same facility name in exactly one other state
            if not facilities.get((_entity_key(debtor), state)):
                other = _other_state_exact(debtor, state, national)
                if other:
                    ccn, provider, other_state = other
                    logger.info(f"  CMS  [{state}] {debtor[:45]:<45} → {provider} ({ccn}, {other_state}, other state)")
                    if not dry_run:
                        _record_cms_match(conn, deal_id, other_state, ccn, provider, debtor,
                                          "ucc_debtor_exact_other_state")
                    linked.setdefault(deal_id, set()).add(ccn)
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
                    if ccn:
                        linked.setdefault(deal_id, set()).add(ccn)
                    updated_chow += 1
                    continue

            # Phase 1b: a skilled nursing company named after a town with
            # one home ("CRAIG SNF, LLC" -> the only home in Craig, CO)
            if re.search(r"\b(SNF|NURSING)\b", debtor.upper()):
                town = _town_candidates(debtor, state, by_state)
                if len(town) == 1:
                    ccn, provider = next(iter(town))
                    logger.info(f"  TOWN [{state}] {debtor[:45]:<45} → {provider} ({ccn})")
                    if not dry_run:
                        _record_cms_match(conn, deal_id, state, ccn, provider, debtor, "ucc_debtor_town")
                    linked.setdefault(deal_id, set()).add(ccn)
                    updated_cms += 1
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

            unlinked.append((deal_id, debtor, state, lender, filed))

        # Phase 3: portfolio companions. Repeat while it links anything: a
        # property company linked in one pass gives its master tenant
        # companions to link to in the next
        updated_portfolio = 0
        for _ in range(3):
            still = []
            for deal_id, debtor, state, lender, filed in unlinked:
                companions = set()
                if lender and filed:
                    for other_filed, other_id in by_lender.get((state, _entity_key(lender)), []):
                        if other_id != deal_id and abs((other_filed - filed).days) <= PORTFOLIO_DAYS:
                            companions |= linked.get(other_id, set())
                match = _portfolio_match(debtor, state, companions, by_state, names_by_ccn)
                if not match:
                    still.append((deal_id, debtor, state, lender, filed))
                    continue
                method, homes = match
                logger.info(f"  PORT [{state}] {debtor[:45]:<45} → {'; '.join(p for _, p in homes)} ({method})")
                if not dry_run:
                    for ccn, provider in homes:
                        _record_cms_match(conn, deal_id, state, ccn, provider, debtor, method)
                    with conn.cursor() as cur:
                        cur.execute("UPDATE deals SET facility_names = %s WHERE id = %s",
                                    ([p for _, p in homes], deal_id))
                linked.setdefault(deal_id, set()).update(c for c, _ in homes)
                updated_portfolio += 1
            if len(still) == len(unlinked):
                break
            unlinked = still
        skipped_no_match += len(unlinked)

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
        print(f"  Portfolio companions:  {updated_portfolio}")
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
    return updated_cms + updated_chow + updated_strip + updated_portfolio


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state", default=None, help="Limit to one state code (e.g. KY)")
    parser.add_argument("--loose-report", default=None, help="Write loose-match candidates for review to this CSV")
    parser.add_argument("--strip", action="store_true", help="Also run the LLC-suffix-strip phase (Phase 2)")
    parser.add_argument("--no-chow", action="store_true", help="Skip the CHOW CSV download (Phase 1)")
    args = parser.parse_args()
    run(dry_run=args.dry_run, state_filter=args.state, loose_report=args.loose_report, use_chow=not args.no_chow, strip=args.strip)
