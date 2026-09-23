"""
CMS Ownership dataset matcher.
Matches extracted deals against CMS qhpq-qrm6 (ownership) records
stored in the local Postgres database.
Uses rapidfuzz for fuzzy string matching.
"""

import logging
import difflib
from datetime import date, timedelta
from typing import Optional

from config import get_config

logger = logging.getLogger(__name__)
config = get_config()

# Generic facility-type words that inflate token-intersection scores between
# otherwise-unrelated names (e.g. "Colonial Heights Rehabilitation and Nursing
# Center" vs "Chase City Health and Rehab Center" scored 69/100 on shared
# boilerplate alone). Stripped from both sides before scoring in _difflib_score.
FACILITY_STOPWORDS = {
    "nursing", "home", "rehabilitation", "rehab", "center",
    "centre", "senior", "living", "health", "care", "facility",
    "services", "and", "of", "the", "at", "manor", "house",
    "residence", "community", "communities", "skilled",
}


#  Minimum facility_name_fuzzy score required, against the same CMS record,
# before an owner-name-only match is trusted. An owner name (e.g. "Selectis
# Health") scores identically against every facility that owner runs, so
# without this, matching purely on owner name means the CCN we return is
# effectively whichever same-owner record the DB happened to return first —
# see the "Selectis" false positives from the 45-deal audit.
OWNER_ONLY_MATCH_FACILITY_FLOOR = 40


def match_deal(deal: dict, conn) -> list[dict]:
    """
    Find CMS ownership records that match a deal.
    Returns list of match dicts sorted by score descending.
    """
    candidate_records = _fetch_candidates(deal, conn)
    if not candidate_records:
        return []

    matches = []
    search_names = _get_search_names(deal)

    for record in candidate_records:
        best_score, matched_on, matched_field, facility_score = _score_record(record, search_names)

        # Owner-name-only evidence isn't enough on its own to pin a specific
        # CCN when the owner runs multiple facilities — require some minimum
        # facility-name corroboration against this same record, or skip it.
        owner_only = matched_on in ("owner_name_fuzzy", "owner_name_substring")
        if owner_only and facility_score < OWNER_ONLY_MATCH_FACILITY_FLOOR:
            continue

        if best_score >= config.fuzzy_match_threshold:
            matches.append({
                "ccn":                  record["ccn"],
                "provider_name":        record["provider_name"],
                "owner_name":           record["owner_name"],
                "owner_type":           record["owner_type"],
                "provider_state":       record["provider_state"],
                "ownership_start_date": record["ownership_start_date"],
                "match_score":          best_score,
                "match_method":         matched_on,
                "matched_on_field":     matched_field,
            })

    matches.sort(key=lambda x: x["match_score"], reverse=True)
    logger.info(f"Found {len(matches)} CMS match(es) for deal: {deal.get('acquiring_entity')}")
    return matches


def _fetch_candidates(deal: dict, conn) -> list[dict]:
    """
    Pull candidate CMS ownership records from Postgres
    using state + date window filters to limit the search space.

    UCC-1 filing dates have no fixed relationship to CMS's recorded
    ownership_start_date — a filing gets renewed/continued independently
    of when CMS actually records a change, so a long-tenured individual
    owner's record can be decades older than the filing date. The date
    window assumption only holds for sources reporting a deal that just
    happened (CHOW, RSS, EDGAR), so UCC deals skip it and match on state
    alone.
    """
    states = deal.get("states") or []
    is_ucc = deal.get("extraction_model") == "ucc_filing"

    with conn.cursor() as cur:
        if is_ucc:
            # No date filter, and deliberately no LIMIT/ORDER BY either —
            # capping+ordering by date here would just reintroduce the
            # same bias by dropping the oldest records in high-volume
            # states (e.g. NY has 8,445 records, well past any limit
            # that would fit that pattern).
            if states:
                cur.execute("""
                    SELECT ccn, provider_name, owner_name, owner_type,
                           provider_state, ownership_start_date
                    FROM cms_ownership_records
                    WHERE provider_state = ANY(%s)
                """, (states,))
            else:
                cur.execute("""
                    SELECT ccn, provider_name, owner_name, owner_type,
                           provider_state, ownership_start_date
                    FROM cms_ownership_records
                """)
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

        acq_date = deal.get("acquisition_date")
        date_from, date_to = _date_window(acq_date)

        # Large states have more records — use a higher limit for known high-volume states
        HIGH_VOLUME_STATES = {"CA", "TX", "FL", "NY", "PA", "OH", "IL"}
        limit = 5000 if any(s in HIGH_VOLUME_STATES for s in states) else 2000

        if states:
            cur.execute("""
                SELECT ccn, provider_name, owner_name, owner_type,
                       provider_state, ownership_start_date
                FROM cms_ownership_records
                WHERE provider_state = ANY(%s)
                  AND (ownership_start_date IS NULL
                       OR ownership_start_date BETWEEN %s AND %s)
                ORDER BY ownership_start_date DESC
                LIMIT %s
            """, (states, date_from, date_to, limit))
        else:
            cur.execute("""
                SELECT ccn, provider_name, owner_name, owner_type,
                       provider_state, ownership_start_date
                FROM cms_ownership_records
                WHERE ownership_start_date BETWEEN %s AND %s
                ORDER BY ownership_start_date DESC
                LIMIT %s
            """, (date_from, date_to, limit))

        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _get_search_names(deal: dict) -> list[tuple[str, str]]:
    """
    Return list of (name, field_source) tuples to search against CMS.
    """
    names = []
    if deal.get("acquiring_entity"):
        names.append((deal["acquiring_entity"], "acquiring_entity"))
    for op in deal.get("operator_names") or []:
        names.append((op, "operator_names"))
    for fac in deal.get("facility_names") or []:
        names.append((fac, "facility_names"))
    if deal.get("seller_entity"):
        names.append((deal["seller_entity"], "seller_entity"))
    return names


def _score_record(record: dict, search_names: list[tuple]) -> tuple[int, str, str, int]:
    """
    Score a CMS record against all search names.
    Returns (best_score, match_method, matched_field, best_facility_score).
    best_facility_score is the best facility_name_fuzzy score seen against
    this record regardless of whether it was the overall winner — callers
    use it to sanity-check owner-only matches (see OWNER_ONLY_MATCH_FACILITY_FLOOR).
    """
    best_score = 0
    best_method = ""
    best_field = ""
    best_facility_score = 0

    owner_name = record.get("owner_name") or ""
    provider_name = record.get("provider_name") or ""

    for name, field in search_names:
        if not name:
            continue

        # Score against owner_name using difflib token-set style
        score_owner = _difflib_score(name, owner_name)
        if score_owner > best_score:
            best_score = score_owner
            best_method = "owner_name_fuzzy"
            best_field = field

        # Score against provider_name (facility name)
        score_provider = _difflib_score(name, provider_name)
        if score_provider > best_facility_score:
            best_facility_score = score_provider
        if score_provider > best_score:
            best_score = score_provider
            best_method = "facility_name_fuzzy"
            best_field = field

        # Exact substring bonus
        if name.upper() in owner_name.upper() or owner_name.upper() in name.upper():
            if 95 > best_score:
                best_score = 95
                best_method = "owner_name_substring"
                best_field = field

    return best_score, best_method, best_field, best_facility_score


def _date_window(acq_date: Optional[str]) -> tuple[date, date]:
    """Return a ±6 month window around the acquisition date."""
    if acq_date:
        try:
            from datetime import date as date_type
            d = date_type.fromisoformat(str(acq_date)[:10])
            return d - timedelta(days=180), d + timedelta(days=180)
        except ValueError:
            pass
    today = date.today()
    return today - timedelta(days=365), today + timedelta(days=180)


def _difflib_score(a: str, b: str) -> int:
    """
    Token-set style fuzzy scorer using stdlib only.
    Handles word reordering and extra words — e.g. 'Commonwealth Care'
    correctly matches 'Commonwealth Care of Roanoke Inc' at 90+.

    On production with rapidfuzz available, replace with:
        from rapidfuzz import fuzz
        return fuzz.token_set_ratio(a, b)
    """
    if not a or not b:
        return 0
    # Word-level intersection score (weighted 70%). Strip trailing commas so
    # CMS's "LAST, FIRST" owner_name format tokenizes the same as "FIRST LAST"
    # — otherwise "STEINBERG," never matches "STEINBERG".
    a_words_all = {w.rstrip(",") for w in a.upper().split() if len(w) > 2}
    b_words_all = {w.rstrip(",") for w in b.upper().split() if len(w) > 2}

    # Drop generic facility-type words so two unrelated names don't score
    # high purely on shared boilerplate ("NURSING", "REHAB", "CENTER", ...).
    # Fall back to the unfiltered set if stripping empties a side — e.g. a
    # name that's *only* generic words — rather than scoring 0 or dividing
    # by zero.
    a_words = {w for w in a_words_all if w.lower() not in FACILITY_STOPWORDS} or a_words_all
    b_words = {w for w in b_words_all if w.lower() not in FACILITY_STOPWORDS} or b_words_all
    if not a_words:
        return 0

    intersection = a_words & b_words
    word_score = len(intersection) / len(a_words)
    # Character sequence score as secondary signal (weighted 30%), computed
    # on the same stopword-stripped tokens so shared boilerplate substrings
    # ("NURSING CENTER") don't inflate this half either.
    seq_score = difflib.SequenceMatcher(
        None, " ".join(sorted(a_words)), " ".join(sorted(b_words))
    ).ratio()
    return int((word_score * 0.7 + seq_score * 0.3) * 100)


def determine_stage(matches: list[dict]) -> tuple[str, str]:
    """
    Given a list of matches, determine the deal stage and confidence.
    Returns (stage, confidence).
    """
    if not matches:
        return "detected", None

    best = matches[0]["match_score"]

    if best >= 85:
        return "confirmed", "high"
    elif best >= config.fuzzy_match_threshold:
        return "pending_cms", "partial"
    else:
        return "detected", "low"
