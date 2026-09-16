"""
scripts/relink_cms_ucc.py

Re-run UCC<->CMS matching in reverse: for every unconfirmed deal, check
the accumulated ucc_filings audit table for a corroborating filing --
entirely DB-side, no portal hits. Safe and cheap to re-run any time
(idempotent: already-confirmed deals are excluded from the query, so a
repeat run only ever adds newly-possible matches, e.g. after a fresh UCC
scrape or a CMS ownership refresh adds more raw material to match against).

Uses the existing ucc/integrator.py:match_against_existing_deals logic
(which now also requires the filing's state to overlap the deal's states
-- fixed 2026-09-16, previously unchecked), run filing-by-filing against
the unconfirmed-deals set.

Called automatically by main.py after a --ucc-states run, scoped to just
those states. Run standalone from repo root for a full, all-states pass:
    venv/bin/python3 scripts/relink_cms_ucc.py [STATE1,STATE2,...]
"""
import sys
sys.path.insert(0, "/Users/kitty/Projects/nursing-home-live-feed")

from datetime import datetime
import psycopg2
import psycopg2.extras
from config import get_config
from ucc.base import UCCFiling
from ucc.integrator import match_against_existing_deals, ExistingDeal
from ucc.lender_classifier import classify_secured_party


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y %I:%M:%S %p").date()
    except Exception:
        pass
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y %I:%M %p").date()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None


def relink_cms_ucc(conn, states: list[str] = None, verbose: bool = True) -> int:
    """Returns the number of deals newly confirmed this run. `states`
    (2-letter codes, case-insensitive) scopes both the filings checked
    and the deals considered -- pass None for a full, all-states pass."""
    wanted = {s.upper() for s in states} if states else None

    with conn.cursor() as cur:
        if wanted:
            cur.execute("""
                SELECT state, filing_number, debtor_name, secured_party, filing_date, status
                FROM ucc_filings WHERE state = ANY(%s)
            """, (list(wanted),))
        else:
            cur.execute("""
                SELECT state, filing_number, debtor_name, secured_party, filing_date, status
                FROM ucc_filings
            """)
        filing_rows = cur.fetchall()

    filings = []
    excluded = 0
    for state, filing_number, debtor_name, secured_party, filing_date_str, status in filing_rows:
        classification = classify_secured_party(secured_party or "")
        if not classification.is_acquisition_relevant:
            excluded += 1
            continue
        filings.append(UCCFiling(
            state=state,
            debtor_name=debtor_name or "",
            secured_party_name=secured_party or "",
            filing_number=filing_number,
            filing_date=parse_date(filing_date_str),
            status=status or "active",
        ))
    if verbose:
        print(f"Loaded {len(filings)} acquisition-relevant filings ({excluded} excluded as non-RE/PE)"
              + (f" for states {sorted(wanted)}" if wanted else ""))

    with conn.cursor() as cur:
        if wanted:
            cur.execute("""
                SELECT id, operator_names, facility_names, acquisition_date, lender, states
                FROM deals
                WHERE ucc_confirmed IS NOT TRUE AND states && %s
            """, (list(wanted),))
        else:
            cur.execute("""
                SELECT id, operator_names, facility_names, acquisition_date, lender, states
                FROM deals
                WHERE ucc_confirmed IS NOT TRUE
            """)
        deal_rows = cur.fetchall()

    existing_deals = [
        ExistingDeal(
            id=str(deal_id), operator_names=operator_names or [],
            facility_names=facility_names or [], acquisition_date=acquisition_date,
            lender=lender, states=deal_states or [],
        )
        for deal_id, operator_names, facility_names, acquisition_date, lender, deal_states in deal_rows
    ]
    if verbose:
        print(f"Loaded {len(existing_deals)} unconfirmed deals to check against")

    matches = {}  # deal_id -> (filing, score)
    for filing in filings:
        result = match_against_existing_deals(filing, existing_deals)
        if result is None:
            continue
        deal_id, score = result
        if deal_id not in matches or score > matches[deal_id][1]:
            matches[deal_id] = (filing, score)

    if verbose:
        print(f"\n{len(matches)} previously-unconfirmed deals now match an existing UCC filing")

    if not matches:
        return 0

    with conn.cursor() as cur:
        for deal_id, (filing, score) in matches.items():
            cur.execute("""
                UPDATE deals
                SET ucc_confirmed = true,
                    lender = COALESCE(lender, %s)
                WHERE id = %s
            """, (filing.secured_party_name, deal_id))
    conn.commit()

    if verbose:
        print(f"Updated {len(matches)} deals: ucc_confirmed=true (+ lender backfilled where missing)")
        print("\nSample matches (first 15):")
        for deal_id, (filing, score) in list(matches.items())[:15]:
            print(f"  deal {deal_id}: {filing.state} filing {filing.filing_number!r} "
                  f"({filing.debtor_name!r}, score={score:.0f})")

    return len(matches)


if __name__ == "__main__":
    states_arg = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    conn = psycopg2.connect(get_config().database_url)
    psycopg2.extras.register_uuid()
    relink_cms_ucc(conn, states=states_arg)
    conn.close()
