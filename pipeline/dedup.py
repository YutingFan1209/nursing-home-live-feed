"""
Deal deduplication.
Generates a stable hash for each deal so the same acquisition
reported by multiple sources is stored only once.
"""

import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


GENERIC_OPERATORS = {
    "third-party operator", "third party operator", "undisclosed operator",
    "unnamed operator", "existing operator", "current operator",
    "affiliated operator", "ensign-affiliated", "management company",
}

def make_dedup_hash(deal: dict) -> str:
    """
    Generate a stable dedup hash from deal fields.

    Design principles:
    - Acquirer name: strip legal suffixes, uppercase, strip whitespace
    - States: sorted for stability (ID,OR == OR,ID)
    - Date: year+month only (day varies between sources)
    - Facility count: exact value
    - Deal value: rounded to nearest $10M
    - Operator names: only real named operators, not generic descriptions
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

    # First REAL operator name as tiebreaker — skip generic descriptions
    operators = sorted([
        op.upper().strip()
        for op in (deal.get("operator_names") or [])
        if op and op.strip() and op.lower().strip() not in GENERIC_OPERATORS
        and len(op.strip()) > 5
    ])
    parts.append(operators[0] if operators else "")

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
