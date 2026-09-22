"""
scripts/ucc_gap_check.py

Find deal entity names for a state that have never been covered by the
automated CHOW-derived UCC search-name list. See memory
ucc_chow_name_blind_spot: scraper/ucc.py's ky_terms/ny_terms/oh_org_terms
union the CHOW list with live known_operator_names as of 2026-09-22, but
that only helps going forward -- names discovered before the fix (or for
states like PA that have no state-specific search-name list at all yet)
can still have a real backlog of never-searched names. This script finds
that gap so it can be worked by hand (Playwright for unblocked states,
Claude in Chrome for blocked/fragile ones -- see oh_ucc_ip_blocked and
pa_ucc_incapsula_fragile memories) and fed through scripts/ingest_manual_ucc.py.

Usage:
    venv/bin/python3 scripts/ucc_gap_check.py <STATE>
    venv/bin/python3 scripts/ucc_gap_check.py PA
"""
import sys

sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")
import psycopg2
from config import get_config
from scraper.chow import get_chow_operator_names
from rapidfuzz import fuzz


def norm(s):
    return (s or "").strip().upper()


def find_gap(state: str) -> list[str]:
    """Return deal entity/facility names for `state` that don't fuzzy-match
    (>=85 token_sort_ratio) anything in that state's CHOW buyer-name list."""
    state = state.upper()
    conn = psycopg2.connect(get_config().database_url)
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT name FROM (
            SELECT unnest(operator_names) AS name FROM deals WHERE %s = ANY(states)
            UNION
            SELECT unnest(facility_names) AS name FROM deals WHERE %s = ANY(states)
            UNION
            SELECT acquiring_entity AS name FROM deals WHERE %s = ANY(states) AND acquiring_entity IS NOT NULL
        ) t WHERE name IS NOT NULL AND name != ''
    """, (state, state, state))
    deal_names = [r[0] for r in cur.fetchall()]

    chow_names = get_chow_operator_names(state)
    chow_norm = [norm(n) for n in chow_names]

    never_searched = []
    for n in deal_names:
        nn = norm(n)
        if not any(fuzz.token_sort_ratio(nn, c) >= 85 for c in chow_norm):
            never_searched.append(n)
    return never_searched, len(deal_names), len(chow_names)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    state = sys.argv[1].upper()
    never_searched, n_deal_names, n_chow_names = find_gap(state)
    print(f"{state}: {n_deal_names} distinct deal names, {n_chow_names} CHOW names, "
          f"{len(never_searched)} never searched")
    for n in never_searched:
        print(n)


if __name__ == "__main__":
    main()
