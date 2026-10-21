"""
New licensees: buyer names for the UCC searches before CMS's CHOW file
lists the sale.

The searches in NY, KY, OH, NJ, PA, CA, FL and ID look up debtor names,
mostly CHOW buyers. CHOW publishes a sale a median 383 days after closing
(the 2026-10-19 release), so a buyer's filing was found only after CHOW
named it for 50 of 250 sales in 2024-25 (back-test 2026-10-21). The
licensee's name is public much sooner, in two places:

  - CMS's monthly Provider Information file: a home's Legal Business Name
    changes to the new licensee (87% of CHOW buyers are the home's legal
    name). cms_legal_names keeps every name seen, so a name first seen
    after the home's earlier one is a new licensee.
  - CMS's monthly Ownership file: every current direct owner of the home
    started recently, i.e. the old owners are gone (a sale), and no CHOW
    record names the home's legal name yet. This covers sales from before
    cms_legal_names started keeping history (108 homes on 2026-10-21).
"""

from datetime import date, timedelta

from ucc.integrator import same_entity

# A sale's filings come at closing; CHOW names the buyer about a year later
LOOKBACK = timedelta(days=540)


def new_licensee_names(conn, state: str, today: date = None) -> list[str]:
    since = (today or date.today()) - LOOKBACK
    with conn.cursor() as cur:
        # A name first seen after another name for the same home, and
        # after the history began (the first load records every home's name)
        cur.execute("""
            SELECT n.ccn, n.legal_business_name
            FROM cms_legal_names n JOIN cms_facilities f ON f.ccn = n.ccn
            WHERE f.provider_state = %(state)s AND n.first_seen >= %(since)s
              AND n.first_seen > (SELECT MIN(first_seen) FROM cms_legal_names)
              AND EXISTS (SELECT 1 FROM cms_legal_names o WHERE o.ccn = n.ccn AND o.first_seen < n.first_seen)
        """, {"state": state, "since": since})
        changed = cur.fetchall()
        cur.execute("""
            SELECT f.ccn, f.legal_business_name
            FROM cms_facilities f
            JOIN (SELECT ccn, MIN(ownership_start_date) AS start FROM cms_ownership_records
                  WHERE owner_role ILIKE '%%DIRECT OWNERSHIP%%' AND owner_role NOT ILIKE '%%INDIRECT%%'
                    AND cms_refreshed_at = (SELECT MAX(cms_refreshed_at) FROM cms_ownership_records)
                  GROUP BY ccn) o ON o.ccn = f.ccn
            WHERE f.provider_state = %(state)s AND f.legal_business_name IS NOT NULL AND o.start >= %(since)s
        """, {"state": state, "since": since})
        sold = cur.fetchall()
        cur.execute("""
            SELECT LEFT(r.ccn, 6), r.buyer_name FROM chow_seen_records r
            JOIN cms_facilities f ON LEFT(f.ccn, 6) = LEFT(r.ccn, 6) WHERE f.provider_state = %s
        """, (state,))
        chow = {}
        for ccn, buyer in cur.fetchall():
            chow.setdefault(ccn, []).append(buyer)
    names = {}
    for ccn, name in changed + sold:
        if not any(same_entity(b, name) for b in chow.get(ccn[:6], [])):
            names.setdefault(name.upper(), name)
    return sorted(names.values())
