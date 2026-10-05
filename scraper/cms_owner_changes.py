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
  2. Trusts, estates and ESOPs are skipped: on their own they are estate
     planning or internal transfers, not acquisitions.
  3. Facilities already linked to a tracker deal dated near the change are
     skipped, so news/CHOW deals aren't duplicated.
  4. Changes are grouped by (buyer, start date), so one buyer taking five
     homes on the same day is one portfolio deal.
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

_NOT_A_BUYER = re.compile(r"\bTRUST\b|\bTR\b|\bESTATE OF\b|EMPLOYEE STOCK|\bESOP\b", re.IGNORECASE)


def ensure_source(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sources (name, url, source_type)
            VALUES (%s, %s, 'chow')
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

    buyers = [r for r in rows if not _NOT_A_BUYER.search(r["owner_name"])]

    by_facility = _group_facility_changes(buyers)

    tracked = _tracked_changes(conn, list(by_facility))
    changes = {k: v for k, v in by_facility.items() if k not in tracked}
    sellers = _sellers(conn, sorted({ccn for ccn, _ in changes}))

    # Portfolio grouping: same buyer, same start date
    portfolios: dict[tuple, list[tuple]] = defaultdict(list)
    for (ccn, start), facility_rows in changes.items():
        buyer = _pick_buyer(facility_rows)
        portfolios[(buyer["owner_name"].upper(), start)].append((ccn, buyer, facility_rows))

    articles = [_build_article(start, members, sellers)
                for (_, start), members in sorted(portfolios.items(), key=lambda kv: kv[0][1])]

    _mark_seen(conn, rows)
    logger.info(
        f"CMS ownership changes: {len(rows)} new owner rows -> {len(by_facility)} facility changes, "
        f"{len(tracked)} already tracked, {len(rows) - len(buyers)} trust/estate rows skipped -> "
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
