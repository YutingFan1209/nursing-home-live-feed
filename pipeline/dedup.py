"""
Deal deduplication.
Generates a stable hash for each deal so the same acquisition
reported by multiple sources is stored only once.
"""

import hashlib
import logging
from rapidfuzz import fuzz
logger = logging.getLogger(__name__)


def make_dedup_hash(deal: dict) -> str:
    """
    Generate a stable dedup hash from deal fields.

    Key: acquirer + states + date(YYYY-MM) + facility_count + deal_value
    Operator names are intentionally excluded — they vary across sources
    (seller vs buyer framing, partial names) and caused false non-duplicates.
    """
    parts = []

    # Normalize acquirer
    acquirer = (deal.get("acquiring_entity") or "").upper().strip()
    for suffix in [" LLC", " INC", " CORP", " LTD", " LP", " L.P.", " L.L.C.", " PLLC", " REIT"]:
        acquirer = acquirer.replace(suffix, "")
    acquirer = acquirer.strip()
    parts.append(acquirer)

    # Normalize states (sorted for stability)
    states = sorted([s.upper() for s in (deal.get("states") or [])])
    parts.append(",".join(states))

    # Acquisition date — year+month only
    date = deal.get("acquisition_date") or ""
    parts.append(date[:7])  # "YYYY-MM"

    # Facility count — exact
    count = deal.get("facility_count")
    parts.append(str(count) if count is not None else "")

    # Deal value — rounded to nearest $10M
    value = deal.get("deal_value_m")
    if value:
        rounded = round(value / 10) * 10
        parts.append(str(rounded))
    else:
        parts.append("")

    key = "|".join(parts)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def is_duplicate(hash_val: str, conn) -> bool:
    """
    Check if a deal with this hash already exists in the database.
    conn is a psycopg2 connection.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM deals WHERE dedup_hash = %s LIMIT 1",
            (hash_val,)
        )
        return cur.fetchone() is not None


def deduplicate_batch(deals: list[dict]) -> list[dict]:
    """
    Within a batch of deals (e.g. from one article),
    remove duplicates before hitting the database.
    """
    seen = set()
    unique = []
    for deal in deals:
        h = make_dedup_hash(deal)
        if h not in seen:
            seen.add(h)
            deal["dedup_hash"] = h
            unique.append(deal)
        else:
            logger.debug(f"Duplicate deal within batch, skipping: {deal.get('acquiring_entity')}")
    return unique


# ── Fuzzy (semantic) dedup ───────────────────────────────────────
# make_dedup_hash() requires an exact match on every field, so the same
# deal reported by two articles with different state subsets, facility
# counts, or acquirer name variants ("Ensign Group" vs "The Ensign Group")
# hashes differently and both get stored. This pass runs after insertion
# and looks for near-matches on a looser set of signals instead.

_ACQUIRER_SUFFIXES = [" LLC", " INC", " CORP", " LTD", " LP", " L.P.", " L.L.C.", " PLLC", " REIT"]


def normalize_acquirer(name: str) -> str:
    name = (name or "").upper().strip()
    if name.startswith("THE "):
        name = name[4:]
    for suffix in _ACQUIRER_SUFFIXES:
        name = name.replace(suffix, "")
    return name.strip()


def _states_overlap_ok(a: list, b: list) -> bool:
    """No states on either side is 'no info', not a mismatch — pass.
    Otherwise require >=50% overlap of the smaller set."""
    set_a, set_b = set(a or []), set(b or [])
    if not set_a or not set_b:
        return True
    return len(set_a & set_b) / min(len(set_a), len(set_b)) >= 0.5


def _date_close_ok(a, b) -> bool:
    """Missing date on either side is 'no info' — pass. Otherwise within 30 days."""
    if not a or not b:
        return True
    return abs((a - b).days) <= 30


def _facility_count_close_ok(a, b) -> bool:
    """Missing count on either side is 'no info' — pass (this is the fix
    for the 22-vs-35 case that a strict 'both null' rule would have missed).
    Otherwise within 20% of the larger value."""
    if a is None or b is None:
        return True
    if a == 0 or b == 0:
        return a == b
    return abs(a - b) / max(a, b) <= 0.20


def _facility_names_overlap_ok(a: list, b: list) -> bool:
    """Both sides empty is 'no info' - pass. Otherwise require at least one
    shared facility name (case/whitespace-insensitive). A fuzzy-matched
    acquirer + overlapping states + nearby date can still be two distinct
    transactions (e.g. the same buyer closing separate deals in the same
    state the same month) - zero shared facilities is a strong signal of
    that, so don't merge on acquirer/date/state proximity alone."""
    set_a = {n.strip().upper() for n in (a or []) if n and n.strip()}
    set_b = {n.strip().upper() for n in (b or []) if n and n.strip()}
    if not set_a and not set_b:
        return True
    return bool(set_a & set_b)


def _merge_array_union(a: list, b: list) -> list:
    return sorted(set(a or []) | set(b or []))


def _merge_higher_count(a, b):
    """Take the larger facility_count; missing on either side is 'no info'."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _merge_first_non_null(a, b):
    return a if a is not None else b


def _completeness_score(row: dict) -> int:
    """Higher = more complete. Presence of a known scalar field counts more
    than array length, since a longer states/name array is only meaningful
    once we know the deal is real (has a date, value, or facility count)."""
    score = len(row.get("states") or [])
    score += 10 if row.get("facility_count") is not None else 0
    score += 10 if row.get("deal_value_m") is not None else 0
    score += 10 if row.get("acquisition_date") is not None else 0
    score += len(row.get("operator_names") or [])
    score += len(row.get("facility_names") or [])
    return score


_DEAL_FIELDS = """
    id, acquiring_entity, states, facility_count, deal_value_m,
    acquisition_date, operator_names, facility_names
"""


def find_and_resolve_fuzzy_duplicate(deal_id, conn) -> str:
    """
    Post-insertion semantic dedup pass for a single just-stored deal.
    Compares it against other deals sharing a fuzzy-matched acquiring_entity
    (token_sort_ratio >= 85, normalizing "The X" vs "X" and corp suffixes),
    overlapping states, a nearby acquisition date, a similar facility count,
    and at least one shared facility name (unless both sides list none).
    If a likely duplicate is found, the more complete row (by
    _completeness_score) survives and absorbs the best fields from both:
    states/operator_names/facility_names are unioned, facility_count takes
    the higher value, and deal_value_m takes whichever side is non-null.
    article_id is left as-is on the surviving row — since that row was
    already chosen for being the more complete one, its article_id already
    points at the more complete source. The other row is deleted.

    Does not commit — participates in the caller's transaction.

    Returns the id that should be used for downstream processing (CMS
    matching, stage updates): deal_id unchanged if no merge happened, or
    the id of the surviving row if the just-inserted row was the one
    dropped.
    """
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_DEAL_FIELDS} FROM deals WHERE id = %s", (deal_id,))
        row = cur.fetchone()
        if not row:
            return deal_id
        cols = [d[0] for d in cur.description]
        new_deal = dict(zip(cols, row))

    acquirer = (new_deal.get("acquiring_entity") or "").strip()
    if not acquirer:
        return deal_id
    normalized_new = normalize_acquirer(acquirer)

    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT {_DEAL_FIELDS} FROM deals
            WHERE id != %s AND acquiring_entity IS NOT NULL AND acquiring_entity != ''
        """, (deal_id,))
        cols = [d[0] for d in cur.description]
        others = [dict(zip(cols, r)) for r in cur.fetchall()]

    for other in others:
        if fuzz.token_sort_ratio(normalized_new, normalize_acquirer(other["acquiring_entity"])) < 85:
            continue
        if not _states_overlap_ok(new_deal["states"], other["states"]):
            continue
        if not _date_close_ok(new_deal["acquisition_date"], other["acquisition_date"]):
            continue
        if not _facility_count_close_ok(new_deal["facility_count"], other["facility_count"]):
            continue
        if not _facility_names_overlap_ok(new_deal["facility_names"], other["facility_names"]):
            continue

        keep, drop = (
            (new_deal, other)
            if _completeness_score(new_deal) >= _completeness_score(other)
            else (other, new_deal)
        )
        merged_states = _merge_array_union(keep["states"], drop["states"])
        merged_facility_count = _merge_higher_count(keep["facility_count"], drop["facility_count"])
        merged_deal_value = _merge_first_non_null(keep["deal_value_m"], drop["deal_value_m"])
        merged_operator_names = _merge_array_union(keep["operator_names"], drop["operator_names"])
        merged_facility_names = _merge_array_union(keep["facility_names"], drop["facility_names"])

        with conn.cursor() as cur:
            cur.execute("DELETE FROM deals WHERE id = %s", (drop["id"],))
            cur.execute("""
                UPDATE deals SET
                    states = %s, facility_count = %s, deal_value_m = %s,
                    operator_names = %s, facility_names = %s
                WHERE id = %s
            """, (
                merged_states, merged_facility_count, merged_deal_value,
                merged_operator_names, merged_facility_names, keep["id"],
            ))

        logger.info(
            f"Fuzzy dedup: merged {drop['acquiring_entity']!r} ({drop['id']}) "
            f"into {keep['acquiring_entity']!r} ({keep['id']}) — "
            f"states -> {merged_states}, facility_count -> {merged_facility_count}, "
            f"deal_value_m -> {merged_deal_value}"
        )
        return keep["id"]

    return deal_id
