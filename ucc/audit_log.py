"""
ucc/audit_log.py

Persists every UCC filing seen (regardless of relevance/routing outcome)
to the ucc_filings audit table, and computes detail_url -- a real,
one-click per-filing deep link where the portal supports one. Called from
main.py:_process_ucc_filing for every filing the live pipeline processes.

Per-state deep link support (confirmed working, no auth/challenge needed
beyond what the search itself already required):
  NY: OnlineLienInformation?lienId=...   (2026-09-15)
  KY: search.aspx?filing=...              (2026-09-16)
  OH, PA, CA: none known yet -- falls back to the portal's search page.
"""
from __future__ import annotations
from ucc.base import UCCFiling


def _detail_url(filing: UCCFiling) -> str | None:
    """Returns None for states without a known per-filing permalink,
    which should fall back to linking at the portal's search page
    instead (see scripts/export_deals.py)."""
    internal_id = (filing.raw or {}).get("internal_id")
    if not internal_id:
        return None
    if filing.state == "NY":
        return f"https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineLienInformation?lienId={internal_id}"
    if filing.state == "KY":
        return f"https://web.sos.ky.gov/ftucc/search.aspx?filing={internal_id}"
    return None


def save_ucc_filings(filings: list[UCCFiling], conn) -> int:
    from psycopg2.extras import execute_values
    from ucc.lender_classifier import classify_secured_party, to_confidence_label
    if not filings:
        return 0
    # Some portals' result sets can list the same (state, filing_number)
    # more than once (e.g. matched via multiple query variants) -- ON
    # CONFLICT DO UPDATE can't affect the same row twice within one
    # execute_values batch, so dedupe first (last occurrence wins).
    filings = list({(f.state, f.filing_number): f for f in filings}.values())
    rows = [(
        f.state, f.filing_number, f.debtor_name, f.secured_party_name if f.secured_party_name else None,
        f.filing_date.isoformat() if f.filing_date else None,
        f.filing_type, f.collateral_description or "", f.status,
        f.raw.get("query_name", ""), f.raw.get("sp_address", ""),
        to_confidence_label(classify_secured_party(f.secured_party_name)) if f.secured_party_name else None,
        _detail_url(f),
    ) for f in filings]
    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO ucc_filings
                (state, filing_number, debtor_name, secured_party,
                 filing_date, filing_type, collateral_description,
                 status, query_name, sp_address, confidence, detail_url)
            VALUES %s
            ON CONFLICT (state, filing_number) DO UPDATE
            SET confidence = EXCLUDED.confidence,
                secured_party = EXCLUDED.secured_party,
                detail_url = COALESCE(EXCLUDED.detail_url, ucc_filings.detail_url)
        """, rows)
    conn.commit()
    return len(rows)
