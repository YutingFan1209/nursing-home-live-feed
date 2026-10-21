"""
Cross-source duplicates: one sale stored as two ownership deals.

The insert-time fuzzy dedup (pipeline/dedup.py) needs near-identical buyer
names and dates within 30 days, so the same sale seen by different sources
slips through: a state CON filing names the filing entity months before
closing, news names the parent, CMS CHOW names the new licensee 1-3 months
after (2026-10-07 audit: ~30 groups, e.g. Crescent City Post Acute from CA
OHCA and CHOW, Ensign / Woodland Health stored from 5 articles).

Pairs are ownership deals (not UCC, not dismissed) that share a CMS
facility (CCN) or a facility name in a shared state, dated within
WINDOW_DAYS. A pair is merged automatically only when nothing conflicts:
the buyers match (or one side names none), the sellers don't contradict,
and a state filing isn't dated after the CMS record of the closing. Also
merged automatically: a CHOW record and a CMS ownership-file record of the
same home (both linked by CCN, not by name) within LICENSEE_PARENT_DAYS --
CHOW names the new licensee and the ownership file its parent, so their
buyers never match (2026-10-20: 25 such pairs from one quarterly file). Two different named buyers can be two real transactions (an
opco and a propco sold separately, a REIT and its operator), so those
pairs go to a review list instead.

News and SEC filings about a portfolio sale often name no facility, so
they also pair on the same seller and the same price (NHI's $560 million
NHC portfolio sale, from an 8-K and a Skilled Nursing News story,
audit 2026-10-21).

A merge keeps the more complete row, fills its blanks from the other,
takes the earliest date (the first signal), unions states, operators and
facilities, carries over CMS matches and UCC confirmation, and marks the
other row dismissed with merged_into pointing at the kept row, so the site
can still list its source. Nothing is deleted.
"""

import json
import logging
import re
from datetime import date

from rapidfuzz import fuzz

from pipeline.dedup import _completeness_score, normalize_acquirer

logger = logging.getLogger(__name__)

WINDOW_DAYS = 183
BUYER_MATCH = 85
AFTER_CLOSING_DAYS = 30
LICENSEE_PARENT_DAYS = 7
DIRECT_METHODS = ("chow_ccn_direct", "cms_ownership_direct")

_FIELDS = """d.id, d.acquiring_entity, d.seller_entity, d.operator_names, d.facility_names, d.states,
             d.facility_count, d.deal_value_m, d.acquisition_date, d.stage, d.ucc_confirmed,
             d.extraction_model, d.created_at, s.source_type, a.url"""

_OWNERSHIP = """FROM deals d JOIN articles a ON a.id = d.article_id JOIN sources s ON s.id = a.source_id
                WHERE d.stage <> 'dismissed' AND COALESCE(d.extraction_model, '') <> 'ucc_filing'"""

_FAC_NOISE = re.compile(
    r"\b(LLC|INC|THE|OF|AND|CENTER|CTR|NURSING|REHABILITATION|REHAB|HEALTHCARE|HEALTH|CARE|FOR|AT)\b")


def _fac_key(name: str) -> str:
    key = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    return " ".join(_FAC_NOISE.sub(" ", key).split())


def _names_agree(a: str, b: str) -> bool:
    """Blank on either side is 'no info', not a conflict."""
    if not (a or "").strip() or not (b or "").strip():
        return True
    return fuzz.token_set_ratio(normalize_acquirer(a), normalize_acquirer(b)) >= BUYER_MATCH


def _filed_after_closing(con: dict, closed: dict) -> bool:
    """A CON filing comes before closing; one dated well after the CMS
    record of the closing is more likely the facility's next sale (MI's
    West Bloomfield LOI, four months after its CHOW)."""
    is_cms = closed["source_type"] == "chow" or closed["extraction_model"] == "cms_owner_change"
    if con["source_type"] != "con" or not is_cms:
        return False
    dc, dm = con["acquisition_date"], closed["acquisition_date"]
    return bool(dc and dm and (dc - dm).days > AFTER_CLOSING_DAYS)


def _licensee_and_parent(x: dict, y: dict) -> bool:
    """One CHOW record and one CMS ownership-file record, both tied to a
    shared home by its CCN, dated within LICENSEE_PARENT_DAYS."""
    models = {x["extraction_model"], y["extraction_model"]}
    if models != {"chow_direct", "cms_owner_change"}:
        return False
    if not (x.get("direct_ccns", set()) & y.get("direct_ccns", set())):
        return False
    dx, dy = x["acquisition_date"], y["acquisition_date"]
    return bool(dx and dy and abs((dx - dy).days) <= LICENSEE_PARENT_DAYS)


def _load(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_FIELDS} {_OWNERSHIP}")
        cols = [c[0] for c in cur.description]
        deals = {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}
        cur.execute("SELECT deal_id, ccn, match_method FROM cms_matches WHERE ccn IS NOT NULL")
        for deal_id, ccn, method in cur.fetchall():
            if deal_id in deals:
                deals[deal_id].setdefault("ccns", set()).add(ccn)
                if method in DIRECT_METHODS:
                    deals[deal_id].setdefault("direct_ccns", set()).add(ccn)
    return deals


def find_pairs(conn, only_ids=None) -> tuple[list, list]:
    """(auto, review): lists of (deal_a, deal_b, reason). only_ids limits it
    to pairs involving those deals (the deals a run just added)."""
    deals = _load(conn)
    by_ccn, by_fac, by_price = {}, {}, {}
    for d in deals.values():
        for ccn in d.get("ccns", ()):
            by_ccn.setdefault(ccn, set()).add(d["id"])
        for f in d["facility_names"] or []:
            k = _fac_key(f)
            if len(k) >= 6:
                by_fac.setdefault(k, set()).add(d["id"])
        seller = normalize_acquirer(d["seller_entity"] or "")
        if seller and d["deal_value_m"] and d["deal_value_m"] >= 1:
            by_price.setdefault(f"{seller} ${float(d['deal_value_m']):.1f}M", set()).add(d["id"])

    candidates = {}
    for kind, index in (("same CMS facility", by_ccn), ("same facility name", by_fac),
                        ("same seller and price", by_price)):
        for key, ids in index.items():
            for a in ids:
                for b in ids:
                    if a < b and (only_ids is None or a in only_ids or b in only_ids):
                        candidates.setdefault((a, b), f"{kind}: {key}")

    auto, review = [], []
    for (a, b), reason in candidates.items():
        x, y = deals[a], deals[b]
        if reason.startswith("same facility name") and not set(x["states"] or []) & set(y["states"] or []):
            continue
        dx, dy = x["acquisition_date"], y["acquisition_date"]
        if dx and dy and abs((dx - dy).days) > WINDOW_DAYS:
            continue
        if _filed_after_closing(x, y) or _filed_after_closing(y, x):
            review.append((x, y, reason + " (state filing dated after the CMS record: maybe a later sale)"))
        elif _names_agree(x["acquiring_entity"], y["acquiring_entity"]) and _names_agree(x["seller_entity"], y["seller_entity"]):
            auto.append((x, y, reason))
        elif _licensee_and_parent(x, y):
            auto.append((x, y, reason + " (CHOW licensee + CMS ownership parent)"))
        else:
            review.append((x, y, reason))
    return auto, review


def merge(keep: dict, drop: dict, conn) -> bool:
    """Fold drop into keep and dismiss drop. Returns False (nothing changed)
    if the merged row would collide with another deal on the semantic
    unique index."""
    dates = [d for d in (keep["acquisition_date"], drop["acquisition_date"]) if d]
    counts = [c for c in (keep["facility_count"], drop["facility_count"]) if c is not None]
    union = lambda k: sorted({v for v in (keep[k] or []) + (drop[k] or []) if v})
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT same_sale")
        try:
            cur.execute("""
                UPDATE deals SET
                    acquiring_entity = COALESCE(NULLIF(acquiring_entity, ''), %s),
                    seller_entity = COALESCE(NULLIF(seller_entity, ''), %s),
                    operator_names = %s, facility_names = %s, states = %s,
                    facility_count = %s, deal_value_m = COALESCE(deal_value_m, %s),
                    acquisition_date = %s,
                    stage = CASE WHEN %s = 'confirmed' THEN 'confirmed' ELSE stage END,
                    ucc_confirmed = ucc_confirmed OR %s, updated_at = NOW()
                WHERE id = %s
            """, (drop["acquiring_entity"], drop["seller_entity"], union("operator_names"),
                  union("facility_names"), union("states"), max(counts) if counts else None,
                  drop["deal_value_m"], min(dates) if dates else None, drop["stage"],
                  bool(drop["ucc_confirmed"]), keep["id"]))
            cur.execute("""
                INSERT INTO cms_matches (deal_id, ccn, provider_name, owner_name, owner_type, provider_state,
                                         ownership_start_date, match_score, match_method, matched_on_field, verified)
                SELECT %s, ccn, provider_name, owner_name, owner_type, provider_state,
                       ownership_start_date, match_score, match_method, matched_on_field, verified
                FROM cms_matches m WHERE deal_id = %s
                  AND NOT EXISTS (SELECT 1 FROM cms_matches k WHERE k.deal_id = %s AND k.ccn IS NOT DISTINCT FROM m.ccn)
            """, (keep["id"], drop["id"], keep["id"]))
            cur.execute("UPDATE deals SET stage = 'dismissed', merged_into = %s, updated_at = NOW() WHERE id = %s",
                        (keep["id"], drop["id"]))
            cur.execute("RELEASE SAVEPOINT same_sale")
        except Exception as e:
            if "unique" not in str(e).lower():
                raise
            cur.execute("ROLLBACK TO SAVEPOINT same_sale")
            logger.warning(f"Same-sale merge {drop['id']} -> {keep['id']} skipped: {e}")
            return False
    logger.info(f"Same sale: merged {drop['id']} ({drop['source_type']}) into {keep['id']} ({keep['source_type']})")
    return True


def apply_pairs(pairs: list, conn, log_path: str = None) -> int:
    """Merge each pair, more complete row kept. A row already merged away in
    this pass is skipped (a group of five stories collapses pair by pair).
    log_path gets one JSON line per merge with both rows as they were."""
    dismissed, merged = set(), 0
    log = open(log_path, "a") if log_path else None
    for x, y, reason in pairs:
        if x["id"] in dismissed or y["id"] in dismissed:
            continue
        keep, drop = (x, y) if _completeness_score(x) >= _completeness_score(y) else (y, x)
        # Growing the kept row to the other's facility count can make it
        # identical to the other row (unique index): keep the other instead
        ok = merge(keep, drop, conn)
        if not ok and merge(drop, keep, conn):
            keep, drop, ok = drop, keep, True
        if ok:
            dismissed.add(drop["id"])
            merged += 1
            if log:
                log.write(json.dumps({"keep": keep, "drop": drop, "reason": reason}, default=str) + "\n")
    if log:
        log.close()
    return merged


def resolve_new_deals(conn, deal_ids) -> tuple[int, int]:
    """End-of-run pass over the deals a run added: merge the clear cases,
    log the ones that need a person. Returns (merged, needs review)."""
    if not deal_ids:
        return 0, 0
    auto, review = find_pairs(conn, only_ids=set(deal_ids))
    merged = apply_pairs(auto, conn)
    for x, y, reason in review:
        logger.info(f"Same sale? review: {x['acquiring_entity']!r} ({x['source_type']}) vs "
                    f"{y['acquiring_entity']!r} ({y['source_type']}) -- {reason}")
    return merged, len(review)
