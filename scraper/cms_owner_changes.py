"""
CMS ownership-change detector.

The monthly CMS nursing home Ownership file (loaded into
cms_ownership_records by cms/fetch_cms.py) lists every current owner of
every Medicare SNF with the date that owner's association began. A new
organization owner with a recent start date is a sale that has already
closed. The file is refreshed monthly with a lag of about 1-2 months, far
fresher than the CHOW file (whose newest effective date was still
2026-02-01 in September 2026). Backtest 2026-09-30 (docs/con-feasibility.md):
211 facilities got a new 5%+ direct owner in the prior 12 months and 116 of
them weren't linked to any tracker deal.

How a change becomes a deal:
  1. Candidate rows: organization owners in the latest refresh, with a
     5%+ direct, direct or 5%+ indirect ownership role, a start date within
     RECENCY_DAYS, and no earlier association with the same facility (a new
     role for an existing owner isn't a sale).
  2. Trusts, estates, ESOPs, foundations and hospital districts are
     skipped: on their own they are estate planning, internal transfers,
     gifts or Medicaid financing set-ups (TX hospital districts), not
     acquisitions.
  2b. A change is skipped when an owner of the facility from before it
     (5%+ direct/indirect, organization or person) is still listed: the old
     owners didn't sell, so it's a restructure or a new parent above them
     (36 of 117 in the 2026-11-11 backfill: holdco reshuffles, Risant over
     Cone Health, Sanford over Marshfield, hospital-district set-ups).
  3. Facilities already linked to a tracker deal dated near the change are
     skipped, so news/CHOW deals aren't duplicated.
  4. Changes on the same start date that share any new owner are one
     portfolio deal (one buyer taking five homes, or MRCMM / MRCMM II /
     MRCMM III under Midwest SNF Holdings on one day).
  5. The seller is an organization direct owner on that facility that
     dropped out of the latest refresh. This only exists when the change
     fell between two loaded refreshes, so older changes have no seller.

Every candidate key is recorded in cms_owner_change_seen whether or not it
becomes a deal, so each change is evaluated exactly once.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from pipeline.run_health import health

logger = logging.getLogger(__name__)

SOURCE_NAME = "CMS Nursing Home Ownership Changes"
SOURCE_URL = "https://data.cms.gov/provider-data/dataset/y2hd-n93e"

# CMS publishes the Ownership file monthly; reload once the stored copy is
# older than this
REFRESH_MAX_AGE_DAYS = 25

# How far back an owner's start date can be and still become a deal. On
# first activation this backfills a year of changes.
RECENCY_DAYS = 365

# A tracker deal on the same facility within this many days of the change
# is taken to be the same transaction
TRACKED_WINDOW_DAYS = 180

# New owners on the same facility within this many days of each other are
# one transaction (e.g. two fund entities of the same PE sponsor)
SAME_CHANGE_DAYS = 45

# An owner associated with the facility this long before the "new" start
# date was already there (CMS sometimes restates dates by a few days)
PRIOR_OWNER_GRACE_DAYS = 30

DIRECT_ROLES = ("5% OR GREATER DIRECT OWNERSHIP INTEREST", "DIRECT OWNERSHIP INTEREST")
INDIRECT_ROLES = ("5% OR GREATER INDIRECT OWNERSHIP INTEREST",)

_NOT_A_BUYER = re.compile(r"\bTRUST\b|\bTR\b|\bESTATES?\b|EMPLOYEE STOCK|\bESOP\b|\bFOUNDATION\b|"
                         r"HOSPITAL (?:DISTRICT|AUTHORITY|BOARD)|HEALTH ?CARE DISTRICT|SERVICE DIST", re.IGNORECASE)
# Texas nursing homes taken over by a public hospital are Medicaid supplemental-
# payment (QIPP) set-ups with the private operator staying on, not sales
_TX_HOSPITAL = re.compile(r"HOSPITAL|MEDICAL CENTER|HEALTH ?CARE SYSTEMS?\b", re.IGNORECASE)
_FAMILY_NOISE = re.compile(r"\b(LLC|L L C|INC|CORP|CO|LP|HOLDINGS?|HOLD|HOLDCO|[IVX]+|\d+)\b")


def _family(name: str) -> str:
    """'OM HOLDCO 7 LLC' and 'OM HOLD CO 9 LLC' -> 'OM'."""
    s = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper()).replace("HOLDCO", "HOLD CO")
    return "".join(_FAMILY_NOISE.sub(" ", s).split())


def ensure_source(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sources (name, url, source_type, last_fetched_at)
            VALUES (%s, %s, 'chow', NOW())
            ON CONFLICT (url) DO UPDATE SET last_fetched_at = NOW()
            RETURNING id
        """, (SOURCE_NAME, SOURCE_URL))
        return cur.fetchone()[0]


def refresh_ownership_if_stale(conn) -> bool:
    """Reload the CMS Ownership file when the stored copy is older than
    REFRESH_MAX_AGE_DAYS. Returns True if a reload ran."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(cms_refreshed_at) FROM cms_ownership_records")
        last = cur.fetchone()[0]
    if last and datetime.now(timezone.utc) - last < timedelta(days=REFRESH_MAX_AGE_DAYS):
        logger.info(f"CMS Ownership file is current (loaded {last:%Y-%m-%d})")
        return False
    from cms.fetch_cms import load_ownership
    logger.info("CMS Ownership file is stale; reloading")
    load_ownership(conn)
    return True


def refresh_provider_info_if_stale(conn) -> bool:
    """Same for the Provider Information file, which carries each home's
    licensed company (Legal Business Name): a new one is a buyer CHOW
    hasn't listed yet (scraper/licensee_names.py), and its street address,
    which UCC debtors are often named by."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(cms_refreshed_at), LEAST(COUNT(legal_business_name), COUNT(provider_address)) FROM cms_facilities")
        last, named = cur.fetchone()
    if named and last and datetime.now(timezone.utc) - last < timedelta(days=REFRESH_MAX_AGE_DAYS):
        return False
    from matcher.carecompare import load_care_compare
    logger.info("CMS Provider Information file is stale; reloading")
    load_care_compare(conn)
    conn.commit()
    return True


def _candidate_rows(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""
            WITH latest AS (SELECT MAX(cms_refreshed_at) AS m FROM cms_ownership_records)
            SELECT o.ccn, o.owner_name, o.owner_role, o.ownership_start_date,
                   o.ownership_percentage, f.provider_name, f.provider_state
            FROM cms_ownership_records o
            JOIN latest ON o.cms_refreshed_at = latest.m
            JOIN cms_facilities f ON f.ccn = o.ccn
            WHERE o.owner_type = 'Organization'
              AND o.owner_role = ANY(%(roles)s)
              AND o.ownership_start_date >= %(since)s
              AND NOT EXISTS (
                  SELECT 1 FROM cms_ownership_records p
                  WHERE p.ccn = o.ccn
                    AND upper(p.owner_name) = upper(o.owner_name)
                    AND p.ownership_start_date < o.ownership_start_date - %(grace)s
              )
              AND NOT EXISTS (
                  SELECT 1 FROM cms_owner_change_seen s
                  WHERE s.ccn = o.ccn AND s.owner_name = o.owner_name
                    AND s.start_date = o.ownership_start_date
              )
        """, {
            "roles": list(DIRECT_ROLES + INDIRECT_ROLES),
            "since": date.today() - timedelta(days=RECENCY_DAYS),
            "grace": PRIOR_OWNER_GRACE_DAYS,
        })
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _tracked_changes(conn, changes: list[tuple[str, date]]) -> set[tuple[str, date]]:
    """(ccn, start date) changes already covered by a live tracker deal on
    that facility dated near the change."""
    if not changes:
        return set()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT m.ccn, COALESCE(d.acquisition_date, d.created_at::date)
            FROM cms_matches m JOIN deals d ON d.id = m.deal_id
            WHERE m.ccn = ANY(%s) AND d.stage <> 'dismissed'
              AND COALESCE(m.match_score, 100) >= 90
        """, (sorted({ccn for ccn, _ in changes}),))
        deal_dates = defaultdict(list)
        for ccn, deal_date in cur.fetchall():
            if deal_date:
                deal_dates[ccn].append(deal_date)
    return {
        (ccn, start) for ccn, start in changes
        if any(abs((d - start).days) <= TRACKED_WINDOW_DAYS for d in deal_dates[ccn])
    }


def _continuing_owners(conn, ccns: list[str]) -> dict[str, list[tuple[str, date]]]:
    """ccn -> (owner, start) of every 5%+ direct/indirect owner in the latest refresh."""
    if not ccns:
        return {}
    with conn.cursor() as cur:
        cur.execute("""
            WITH latest AS (SELECT MAX(cms_refreshed_at) AS m FROM cms_ownership_records)
            SELECT o.ccn, o.owner_name, o.ownership_start_date
            FROM cms_ownership_records o, latest
            WHERE o.cms_refreshed_at = latest.m AND o.ccn = ANY(%s) AND o.owner_role = ANY(%s)
              AND o.ownership_start_date IS NOT NULL
        """, (ccns, list(DIRECT_ROLES + INDIRECT_ROLES)))
        out: dict[str, list] = defaultdict(list)
        for ccn, name, start in cur.fetchall():
            out[ccn].append((name, start))
        return out


def _old_owner_stays(change: tuple[str, date], continuing: dict) -> bool:
    ccn, start = change
    return any(s < start - timedelta(days=PRIOR_OWNER_GRACE_DAYS) for _, s in continuing.get(ccn, ()))


def _same_day_orgs(conn, keys: list[tuple]) -> dict[tuple, set[str]]:
    """(ccn, start) -> organization owners in any role starting that day
    (shared property/holding companies tie a split portfolio together)."""
    if not keys:
        return {}
    with conn.cursor() as cur:
        cur.execute("""
            WITH latest AS (SELECT MAX(cms_refreshed_at) AS m FROM cms_ownership_records)
            SELECT o.ccn, o.ownership_start_date, upper(o.owner_name)
            FROM cms_ownership_records o, latest
            WHERE o.cms_refreshed_at = latest.m AND o.owner_type = 'Organization'
              AND o.ccn = ANY(%s) AND o.ownership_start_date = ANY(%s)
        """, (sorted({k[0] for k in keys}), sorted({k[1] for k in keys})))
        out: dict[tuple, set] = defaultdict(set)
        for ccn, start, name in cur.fetchall():
            if (ccn, start) in keys and not _NOT_A_BUYER.search(name):
                out[(ccn, start)].add(name)
        return out


def _portfolios(changes: dict[tuple, list[dict]], extra_owners: dict | None = None) -> list[list[tuple]]:
    """Facility changes on one start date that share a new owner -> one group."""
    keys = sorted(changes, key=lambda k: (k[1], k[0]))
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    first_by_owner: dict[tuple, tuple] = {}
    for k in keys:
        for name in {r["owner_name"].upper() for r in changes[k]} | (extra_owners or {}).get(k, set()):
            ok = (name, k[1])
            if ok in first_by_owner:
                parent[find(k)] = find(first_by_owner[ok])
            else:
                first_by_owner[ok] = k
    groups: dict[tuple, list] = defaultdict(list)
    for k in keys:
        groups[find(k)].append(k)
    return list(groups.values())


def _sellers(conn, ccns: list[str]) -> dict[str, str]:
    """Organization direct owners that dropped out of the latest refresh."""
    if not ccns:
        return {}
    with conn.cursor() as cur:
        cur.execute("""
            WITH latest AS (SELECT MAX(cms_refreshed_at) AS m FROM cms_ownership_records)
            SELECT DISTINCT ON (o.ccn) o.ccn, o.owner_name
            FROM cms_ownership_records o, latest
            WHERE o.ccn = ANY(%s) AND o.cms_refreshed_at < latest.m
              AND o.owner_type = 'Organization' AND o.owner_role = ANY(%s)
            ORDER BY o.ccn, o.ownership_percentage DESC NULLS LAST, o.ownership_start_date DESC
        """, (ccns, list(DIRECT_ROLES)))
        return dict(cur.fetchall())


def _group_facility_changes(rows: list[dict]) -> dict[tuple, list[dict]]:
    """One change per facility, keyed by its earliest start date; new owners
    on the same facility within SAME_CHANGE_DAYS of it join that change."""
    changes: dict[tuple, list[dict]] = {}
    for r in sorted(rows, key=lambda r: (r["ccn"], r["ownership_start_date"])):
        key = next((k for k in changes if k[0] == r["ccn"]
                    and (r["ownership_start_date"] - k[1]).days <= SAME_CHANGE_DAYS), None)
        changes.setdefault(key or (r["ccn"], r["ownership_start_date"]), []).append(r)
    return changes


def _mark_seen(conn, rows: list[dict]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO cms_owner_change_seen (ccn, owner_name, start_date) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [(r["ccn"], r["owner_name"], r["ownership_start_date"]) for r in rows],
        )
    conn.commit()


def _pick_buyer(rows: list[dict]) -> dict:
    """The facility's new direct owner if there is one, else its new
    indirect parent; largest stake first."""
    return sorted(rows, key=lambda r: (r["owner_role"] not in DIRECT_ROLES,
                                       -(r["ownership_percentage"] or 0),
                                       r["owner_name"]))[0]


def fetch_cms_owner_change_deals(conn) -> list[dict]:
    """New ownership changes in the CMS Ownership file, as pre-extracted
    articles (one per buyer + start date) ready for process_article."""
    rows = _candidate_rows(conn)
    if not rows:
        logger.info("CMS ownership changes: no new candidate owners")
        return []

    buyers = [r for r in rows if not _NOT_A_BUYER.search(r["owner_name"])
              and not (r["provider_state"] == "TX" and _TX_HOSPITAL.search(r["owner_name"]))]

    by_facility = _group_facility_changes(buyers)

    tracked = _tracked_changes(conn, list(by_facility))
    changes = {k: v for k, v in by_facility.items() if k not in tracked}
    continuing = _continuing_owners(conn, sorted({ccn for ccn, _ in changes}))
    restructures = [k for k in changes if _old_owner_stays(k, continuing)]
    changes = {k: v for k, v in changes.items() if k not in restructures}
    sellers = _sellers(conn, sorted({ccn for ccn, _ in changes}))

    articles, internal = [], 0
    for group in sorted(_portfolios(changes, _same_day_orgs(conn, list(changes))), key=lambda g: g[0][1]):
        members = [(ccn, _pick_buyer(changes[(ccn, start)]), changes[(ccn, start)]) for ccn, start in group]
        # the deal's buyer: the owner named on the most facilities, then the usual pick
        counts = defaultdict(int)
        for _, b, _ in members:
            counts[b["owner_name"].upper()] += 1
        lead = max(counts, key=lambda n: (counts[n], -len(n)))
        members.sort(key=lambda m: m[1]["owner_name"].upper() != lead)
        seller_fams = {_family(sellers[c]) for c, _, _ in members if c in sellers}
        if _family(lead) and _family(lead) in seller_fams:
            internal += len(members)  # "OM HOLDCO 7" -> "OM HOLD CO 9": same owner group
            continue
        articles.append(_build_article(group[0][1], members, sellers))

    _mark_seen(conn, rows)
    logger.info(
        f"CMS ownership changes: {len(rows)} new owner rows -> {len(by_facility)} facility changes, "
        f"{len(tracked)} already tracked, {len(restructures) + internal} restructures (old owner still listed "
        f"or seller in the buyer's group), "
        f"{len(rows) - len(buyers)} trust/estate/foundation/district rows skipped -> "
        f"{len(articles)} deals"
    )
    health.attempted("CMS ownership changes", len(articles))
    return articles


def _build_article(start: date, members: list[tuple], sellers: dict[str, str]) -> dict:
    buyer = members[0][1]["owner_name"]
    ccns = [ccn for ccn, _, _ in members]
    facilities = [b["provider_name"] for _, b, _ in members]
    states = sorted({b["provider_state"] for _, b, _ in members if b["provider_state"]})
    other_owners = sorted({r["owner_name"] for _, _, rows in members for r in rows} - {buyer})
    seller_names = [sellers[c] for c in ccns if c in sellers]
    seller = max(set(seller_names), key=seller_names.count) if seller_names else None
    roles = sorted({b["owner_role"].lower() for _, b, _ in members})
    start_str = start.isoformat()
    slug = re.sub(r"[^a-z0-9]+", "-", buyer.lower()).strip("-")

    lines = [
        "CMS Nursing Home Ownership file: new owner",
        "",
        f"New owner: {buyer} ({'; '.join(roles)}), associated since {start_str}",
        f"Prior owner no longer listed: {seller or 'not available (change predates loaded CMS refreshes)'}",
        f"Facilities ({len(ccns)}):",
        *[f"  - {b['provider_name']}, {b['provider_state']} (CCN {ccn})" for ccn, b, _ in members],
    ]
    if other_owners:
        lines.append(f"Other new owners on the same date: {'; '.join(other_owners)}")

    return {
        "url": f"{SOURCE_URL}#owner-{slug}-{start_str}",
        "title": f"[CMS Ownership] {buyer} became owner of {len(ccns)} facilit{'y' if len(ccns) == 1 else 'ies'} ({', '.join(states)})",
        "published_at": datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc),
        "raw_text": "\n".join(lines),
        "source_name": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "source_type": "chow",
        "pre_extracted": True,
        "extraction_model": "cms_owner_change",
        "acquiring_entity": buyer,
        "seller_entity": seller,
        "operator_names": [buyer] + other_owners,
        "facility_names": facilities,
        "states": states,
        "facility_count": len(ccns),
        "deal_value_m": None,
        "acquisition_date": start_str,
        "financing_amount_m": None,
        "lender": None,
        "rationale": (
            f"CMS Ownership file lists {buyer} as a new owner of {len(ccns)} "
            f"facilit{'y' if len(ccns) == 1 else 'ies'} since {start_str}"
            + (f"; prior owner {seller} no longer listed." if seller else ".")
        ),
        "ccns": ccns,
        "_cms_change_id": f"{buyer.upper()}|{start_str}",
    }
