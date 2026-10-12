"""
Match UCC filings against a person to the homes that person owns (NY, and
OH, the states whose UCC search includes CMS individual owners; KY was
tried 2026-10-12 and turned off -- see main.py).

NY's UCC search includes CMS individual owners (Tyler's methodology), so
about 850 NY filings name a person as debtor. The filing says nothing about
which home, so this only records which NY homes the debtor owns or controls
in the CMS ownership file, in the filing's own state: one home -> single_home, several -> multi_home
(every one is only a "possible home"), none -> unmatched.

Names are matched on first + last name, as main.py's UCC debtor check does
(CMS "LAST, FIRST" vs the portal's "FIRST M LAST"). Only NY homes and
ownership/control roles count: a common name that is a CMS owner only in
another state (Robert Butler, CT) is not a match.

    venv/bin/python3 -m pipeline.individual_owners   # refresh the table
"""
import logging
from collections import defaultdict

from pipeline.normalizer import _smart_title_case
from pipeline.ucc_category import is_person

logger = logging.getLogger(__name__)

# Same roles main.py searches the UCC portal with
OWNERSHIP_ROLES = (
    '5% OR GREATER DIRECT OWNERSHIP INTEREST',
    '5% OR GREATER INDIRECT OWNERSHIP INTEREST',
    'DIRECT OWNERSHIP INTEREST',
    'INDIRECT OWNERSHIP INTEREST',
    'GENERAL PARTNERSHIP INTEREST',
    'LIMITED PARTNERSHIP INTEREST',
    'OPERATIONAL/MANAGERIAL CONTROL',
    'MANAGING CONTROL - GOVERNING BODY',
)

UCC_TITLE_PREFIX = "UCC-1 filing: "


def first_last(name: str) -> str:
    from ucc.lender_search import norm_name
    parts = norm_name(name).split()
    return f"{parts[0]} {parts[-1]}" if len(parts) >= 2 else " ".join(parts)


# States whose UCC search includes CMS individual owners (main.INDIVIDUAL_UCC_STATES)
STATES = ("NY", "OH")


def cms_owner_index(conn, state: str = "NY") -> dict:
    """first + last name -> {ccn: (provider_name, owner_name, owner_role)}"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT owner_name, ccn, provider_name, owner_role FROM cms_ownership_records
            WHERE provider_state = %s AND owner_type = 'Individual' AND owner_role = ANY(%s)
        """, (state, list(OWNERSHIP_ROLES)))
        index = defaultdict(dict)
        for owner, ccn, provider, role in cur.fetchall():
            if owner and "," in owner:
                last, first = (p.strip() for p in owner.split(",", 1))
                index[first_last(f"{first} {last}")].setdefault(ccn, (provider, owner, role))
        return index


def person_filings(conn) -> list[tuple]:
    """(deal_id, debtor) for live NY UCC deals filed against a person. The
    debtor comes from ucc_filings, else the article title main.py wrote
    ("UCC-1 filing: DEBTOR / LENDER (NY)") -- about half never got a
    ucc_filings row."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT d.id, uf.debtor_name, a.title, split_part(a.url, '/', 3) FROM deals d JOIN articles a ON a.id = d.article_id
            LEFT JOIN ucc_filings uf ON uf.state = split_part(a.url, '/', 3) AND uf.filing_number = split_part(a.url, '/', 4)
            WHERE d.stage <> 'dismissed' AND split_part(a.url, '/', 3) = ANY(%s) AND a.url LIKE 'ucc://%%'
        """, (list(STATES),))
        out = []
        for deal_id, debtor, title, state in cur.fetchall():
            if not debtor and (title or "").startswith(UCC_TITLE_PREFIX):
                debtor = title[len(UCC_TITLE_PREFIX):].split(" / ")[0]
            if is_person(debtor):
                out.append((deal_id, debtor, state))
        return out


def match_type(n_homes: int) -> str:
    return "unmatched" if not n_homes else "single_home" if n_homes == 1 else "multi_home"


UNMATCHED_LINE = "Filed against an individual; no matching home in CMS ownership records."


def owner_line(home_names: list[str]) -> str:
    """The card's one line for a filing against a person. Several homes are
    all only possible: "Owner of X, Y, Z +8 more"."""
    if not home_names:
        return UNMATCHED_LINE
    # CMS provider names are ALL CAPS
    shown = ", ".join(_smart_title_case(n) if n.isupper() else n for n in home_names[:3])
    return f"Owner of {shown}" + (f" +{len(home_names) - 3} more" if len(home_names) > 3 else "")


def refresh(conn) -> dict:
    """Rebuild ucc_individual_matches for every live NY person filing.
    Returns counts: filings, matched, single_home, multi_home, unmatched."""
    indexes = {st: cms_owner_index(conn, st) for st in STATES}
    filings = person_filings(conn)
    rows, counts = [], {"filings": len(filings), "single_home": 0, "multi_home": 0, "unmatched": 0}
    for deal_id, debtor, state in filings:
        homes = indexes[state].get(first_last(debtor), {})
        kind = match_type(len(homes))
        counts[kind] += 1
        rows += [(deal_id, ccn, prov, owner, role, kind) for ccn, (prov, owner, role) in homes.items()]
    counts["matched"] = counts["single_home"] + counts["multi_home"]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM ucc_individual_matches")
        cur.executemany("""INSERT INTO ucc_individual_matches (deal_id, ccn, provider_name, owner_name, owner_role, match_type)
                           VALUES (%s, %s, %s, %s, %s, %s)""", rows)
    conn.commit()
    logger.info("NY individual UCC filings: %(filings)d, matched %(matched)d "
                "(%(single_home)d single-home, %(multi_home)d multi-home), unmatched %(unmatched)d", counts)
    return counts


if __name__ == "__main__":
    import psycopg2
    from config import get_config
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(refresh(psycopg2.connect(get_config().database_url)))
